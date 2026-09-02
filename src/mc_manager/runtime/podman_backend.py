import json
import subprocess
import time
from collections.abc import Sequence
from typing import Any

from mc_manager.runtime.base import RuntimeBackend, RuntimeSpec


class PodmanError(RuntimeError):
    pass


class PodmanRuntime(RuntimeBackend):
    """Rootless Podman backend using the local CLI, without a privileged API socket."""

    def __init__(
        self,
        binary: str = "/usr/bin/podman",
        *,
        cpus: float = 2.0,
        pull_timeout_seconds: int = 900,
    ) -> None:
        self.binary = binary
        self.cpus = cpus
        self.pull_timeout_seconds = pull_timeout_seconds

    @staticmethod
    def _name(run_id: str) -> str:
        return f"mc-{run_id}"

    def _run(
        self,
        arguments: Sequence[str],
        *,
        check: bool = True,
        timeout: int | float = 60,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self.binary, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PodmanError(f"Podman command failed: {arguments[0]}") from error
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown Podman error"
            raise PodmanError(f"podman {arguments[0]}: {message}")
        return result

    def _inspect(self, run_id: str) -> dict[str, Any] | None:
        result = self._run(
            ["inspect", self._name(run_id), "--format", "json"],
            check=False,
        )
        if result.returncode != 0:
            exists = self._run(
                ["container", "exists", self._name(run_id)],
                check=False,
            )
            if exists.returncode == 1:
                return None
            message = result.stderr.strip() or result.stdout.strip() or "inspect failed"
            raise PodmanError(f"podman inspect: {message}")
        payload: Any = json.loads(result.stdout)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise PodmanError("podman inspect returned an invalid response")
        return payload[0]

    def _status(self, run_id: str) -> str | None:
        details = self._inspect(run_id)
        if details is None:
            return None
        state = details.get("State")
        if not isinstance(state, dict):
            raise PodmanError("podman inspect response has no State")
        return str(state.get("Status", "")).lower()

    def start(self, spec: RuntimeSpec) -> str:
        if spec.paper_jar is None:
            raise ValueError("Podman runtime requires a Paper JAR")
        existing = self._inspect(spec.run_id)
        if existing is not None:
            state = existing.get("State", {})
            if str(state.get("Status", "")).lower() != "running":
                self._run(["start", spec.container_name])
            return str(existing.get("Id", spec.container_name))

        image_exists = self._run(["image", "exists", spec.image], check=False)
        if image_exists.returncode != 0:
            self._run(
                ["pull", spec.image],
                timeout=self.pull_timeout_seconds,
            )

        result = self._run(
            [
                "run",
                "--detach",
                "--name",
                spec.container_name,
                "--label",
                "managed-by=mc-minigame-manager",
                "--label",
                f"mc-manager.run-id={spec.run_id}",
                "--log-driver",
                "k8s-file",
                "--workdir",
                "/data",
                "--publish",
                f"127.0.0.1:{spec.port}:25565/tcp",
                "--volume",
                f"{spec.game_path}:/data:rw",
                "--volume",
                f"{spec.paper_jar}:/opt/paper/paper.jar:ro",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,exec,nosuid,nodev,size=256m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                spec.java_memory,
                "--memory-swap",
                spec.java_memory,
                "--pids-limit",
                "512",
                "--cpus",
                str(self.cpus),
                "--restart",
                "no",
                spec.image,
                "java",
                "-Duser.timezone=Asia/Shanghai",
                f"-Xms{spec.java_memory}",
                f"-Xmx{spec.java_memory}",
                "-XX:+UseG1GC",
                "-jar",
                "/opt/paper/paper.jar",
                "--nogui",
            ],
            timeout=120,
        )
        return result.stdout.strip()

    def wait_ready(self, run_id: str, port: int, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._status(run_id) != "running":
                return False
            logs = self._run(
                ["logs", "--tail", "200", self._name(run_id)],
                check=False,
                timeout=10,
            )
            log_ready = "Done (" in f"{logs.stdout}\n{logs.stderr}"
            if log_ready:
                return True
            time.sleep(1)
        return False

    def stop(self, run_id: str, timeout_seconds: int = 120) -> bool:
        details = self._inspect(run_id)
        if details is None:
            return True
        state = details.get("State", {})
        status = str(state.get("Status", "")).lower()
        if status in {"exited", "stopped"}:
            graceful = self._is_graceful_exit(state)
            self._run(["rm", "--force", self._name(run_id)], check=False)
            return graceful

        self._run(
            ["kill", "--signal", "TERM", self._name(run_id)],
            check=False,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current_status = self._status(run_id)
            if current_status is None:
                return True
            if current_status in {"exited", "stopped"}:
                final_details = self._inspect(run_id)
                final_state = (
                    final_details.get("State", {}) if final_details is not None else {}
                )
                graceful = self._is_graceful_exit(final_state)
                self._run(["rm", self._name(run_id)], check=False)
                return graceful
            time.sleep(0.25)

        self._run(
            ["kill", "--signal", "KILL", self._name(run_id)],
            check=False,
        )
        self._run(["rm", "--force", self._name(run_id)], check=False)
        return False

    @staticmethod
    def _is_graceful_exit(state: dict[str, Any]) -> bool:
        return int(state.get("ExitCode", 1)) in {0, 128 + 15} and not bool(
            state.get("OOMKilled", False)
        )

    def exists(self, run_id: str) -> bool:
        return self._status(run_id) in {"created", "running", "restarting", "paused"}

    def managed_run_ids(self) -> set[str]:
        result = self._run(
            [
                "ps",
                "--all",
                "--filter",
                "label=managed-by=mc-minigame-manager",
                "--format",
                "json",
            ]
        )
        payload: Any = json.loads(result.stdout or "[]")
        if not isinstance(payload, list):
            raise PodmanError("podman ps returned an invalid response")
        run_ids: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            labels = item.get("Labels", {})
            if isinstance(labels, dict):
                run_id = labels.get("mc-manager.run-id") or labels.get(
                    "mc-manager.instance-id"
                )
                if run_id:
                    run_ids.add(str(run_id))
        return run_ids

    def smoke_test(self, image: str = "docker.io/library/alpine:3.20") -> str:
        result = self._run(
            ["run", "--rm", image, "printf", "podman-ok"],
            timeout=self.pull_timeout_seconds,
        )
        return result.stdout
