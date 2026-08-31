import json
import subprocess
from pathlib import Path

from mc_manager.config import Settings
from mc_manager.runtime import build_runtime
from mc_manager.runtime.base import RuntimeSpec
from mc_manager.runtime.podman_backend import PodmanRuntime


class RecordingPodman(PodmanRuntime):
    def __init__(self) -> None:
        super().__init__("podman", cpus=1.5)
        self.commands: list[list[str]] = []

    def _inspect(self, run_id: str):
        del run_id
        return None

    def _run(self, arguments, *, check=True, timeout=60):
        del check, timeout
        command = list(arguments)
        self.commands.append(command)
        if command[:2] == ["image", "exists"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "container-id\n", "")


def test_build_runtime_selects_podman(settings: Settings) -> None:
    values = settings.model_dump()
    values["runtime_backend"] = "podman"
    runtime = build_runtime(Settings.model_validate(values))
    assert isinstance(runtime, PodmanRuntime)


def test_podman_start_uses_rootless_safe_flags(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    jar = tmp_path / "paper.jar"
    jar.write_bytes(b"jar")
    runtime = RecordingPodman()
    spec = RuntimeSpec(
        run_id="run-id",
        container_name="mc-run-id",
        game_path=active,
        port=30000,
        image="eclipse-temurin:17-jre",
        java_memory="2g",
        paper_jar=jar,
    )
    assert runtime.start(spec) == "container-id"
    run = next(command for command in runtime.commands if command[0] == "run")
    assert run[
        run.index("--publish") : run.index("--publish") + 2
    ] == ["--publish", "127.0.0.1:30000:25565/tcp"]
    assert "--read-only" in run
    assert "--cap-drop" in run and "ALL" in run
    assert "no-new-privileges" in run
    assert "--restart" in run and "no" in run
    assert str(jar) + ":/opt/paper/paper.jar:ro" in run


def test_managed_run_ids_reads_labels(monkeypatch) -> None:
    runtime = PodmanRuntime("podman")
    payload = [{"Labels": {"mc-manager.run-id": "run-1"}}]

    def fake_run(arguments, *, check=True, timeout=60):
        del check, timeout
        return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")

    monkeypatch.setattr(runtime, "_run", fake_run)
    assert runtime.managed_run_ids() == {"run-1"}
