import os
import socket
import time
from contextlib import suppress
from typing import Any

import docker
from docker.errors import ImageNotFound, NotFound

from mc_manager.runtime.base import RuntimeBackend, RuntimeSpec


class DockerRuntime(RuntimeBackend):
    def __init__(self, base_url: str, *, cpus: float = 2.0) -> None:
        self.client = docker.DockerClient(base_url=base_url)
        self.cpus = cpus

    def _find(self, run_id: str) -> Any | None:
        containers = self.client.containers.list(
            all=True,
            filters={
                "label": [
                    "managed-by=mc-minigame-manager",
                    f"mc-manager.run-id={run_id}",
                ]
            },
        )
        return containers[0] if containers else None

    def start(self, spec: RuntimeSpec) -> str:
        if spec.paper_jar is None:
            raise ValueError("Docker runtime requires a Paper JAR")
        existing = self._find(spec.run_id)
        if existing is not None:
            existing.reload()
            if existing.status != "running":
                existing.start()
            return str(existing.id)

        try:
            self.client.images.get(spec.image)
        except ImageNotFound:
            self.client.images.pull(spec.image)

        memory_bytes = self._parse_memory(spec.java_memory)
        cpu_period = 100_000
        cpu_quota = int(cpu_period * self.cpus)
        run_container: Any = self.client.containers.run
        container = run_container(
            spec.image,
            [
                "java",
                f"-Xms{spec.java_memory}",
                f"-Xmx{spec.java_memory}",
                "-XX:+UseG1GC",
                "-jar",
                "/opt/paper/paper.jar",
                "--nogui",
            ],
            name=spec.container_name,
            detach=True,
            user=f"{os.getuid()}:{os.getgid()}",
            working_dir="/data",
            ports={"25565/tcp": ("127.0.0.1", spec.port)},
            volumes={
                str(spec.game_path): {"bind": "/data", "mode": "rw"},
                str(spec.paper_jar): {"bind": "/opt/paper/paper.jar", "mode": "ro"},
            },
            labels={
                "managed-by": "mc-minigame-manager",
                "mc-manager.run-id": spec.run_id,
            },
            network_disabled=False,
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=256m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=memory_bytes,
            memswap_limit=memory_bytes,
            pids_limit=512,
            cpu_period=cpu_period,
            cpu_quota=cpu_quota,
            restart_policy={"Name": "no"},
        )
        return str(container.id)

    def wait_ready(self, run_id: str, port: int, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            container = self._find(run_id)
            if container is None:
                return False
            container.reload()
            if container.status not in {"created", "running"}:
                return False
            try:
                logs = container.logs(tail=200).decode("utf-8", errors="replace")
                log_ready = 'Done (' in logs or "Done (" in logs
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    if log_ready:
                        return True
            except OSError:
                pass
            time.sleep(1)
        return False

    def stop(self, run_id: str, timeout_seconds: int = 120) -> bool:
        container = self._find(run_id)
        if container is None:
            return True
        container.reload()
        if container.status == "exited":
            state = container.attrs.get("State", {})
            graceful = int(state.get("ExitCode", 1)) == 0 and not state.get("OOMKilled", False)
            with suppress(NotFound):
                container.remove(force=True)
            return graceful

        try:
            container.kill(signal="SIGTERM")
        except NotFound:
            return True
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                container.reload()
            except NotFound:
                return True
            if container.status == "exited":
                with suppress(NotFound):
                    container.remove()
                return True
            time.sleep(0.25)

        with suppress(NotFound):
            container.kill(signal="SIGKILL")
        with suppress(NotFound):
            container.wait(timeout=10)
        with suppress(NotFound):
            container.remove(force=True)
        return False

    def exists(self, run_id: str) -> bool:
        container = self._find(run_id)
        if container is None:
            return False
        container.reload()
        return container.status in {"created", "running", "restarting"}

    def managed_run_ids(self) -> set[str]:
        containers = self.client.containers.list(
            all=True, filters={"label": "managed-by=mc-minigame-manager"}
        )
        result: set[str] = set()
        for container in containers:
            labels = container.labels or {}
            run_id = labels.get("mc-manager.run-id") or labels.get("mc-manager.instance-id")
            if run_id:
                result.add(str(run_id))
        return result

    @staticmethod
    def _parse_memory(value: str) -> int:
        suffixes = {"k": 1024, "m": 1024**2, "g": 1024**3}
        normalized = value.strip().lower()
        if normalized[-1:] in suffixes:
            return int(float(normalized[:-1]) * suffixes[normalized[-1]])
        return int(normalized)
