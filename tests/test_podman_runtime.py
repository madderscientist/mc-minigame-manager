import json
import subprocess
from pathlib import Path

import pytest

from mc_manager.config import Settings
from mc_manager.runtime import build_runtime
from mc_manager.runtime.base import RuntimeSpec
from mc_manager.runtime.podman_backend import PodmanError, PodmanRuntime


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
    assert "--log-driver" in run and "k8s-file" in run
    assert str(jar) + ":/opt/paper/paper.jar:ro" in run
    assert "/tmp:rw,exec,nosuid,nodev,size=256m" in run
    assert "-Duser.timezone=Asia/Shanghai" in run


def test_wait_ready_accepts_paper_done_log(monkeypatch) -> None:
    runtime = PodmanRuntime("podman")
    monkeypatch.setattr(runtime, "_status", lambda _run_id: "running")
    monkeypatch.setattr(
        runtime,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, '[00:12:25 INFO]: Done (9.835s)! For help, type "help"', ""
        ),
    )
    assert runtime.wait_ready("run-id", 30000, 1)


def test_managed_run_ids_reads_labels(monkeypatch) -> None:
    runtime = PodmanRuntime("podman")
    payload = [{"Labels": {"mc-manager.run-id": "run-1"}}]

    def fake_run(arguments, *, check=True, timeout=60):
        del check, timeout
        return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")

    monkeypatch.setattr(runtime, "_run", fake_run)
    assert runtime.managed_run_ids() == {"run-1"}


@pytest.mark.parametrize(
    ("exit_code", "oom_killed", "expected"),
    [(0, False, True), (143, False, True), (1, False, False), (0, True, False)],
)
def test_stop_checks_exit_state_after_term(
    monkeypatch, exit_code: int, oom_killed: bool, expected: bool
) -> None:
    runtime = PodmanRuntime("podman")
    inspections = iter(
        [
            {"State": {"Status": "running"}},
            {
                "State": {
                    "Status": "exited",
                    "ExitCode": exit_code,
                    "OOMKilled": oom_killed,
                }
            },
        ]
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(runtime, "_inspect", lambda _run_id: next(inspections))
    monkeypatch.setattr(runtime, "_status", lambda _run_id: "exited")
    monkeypatch.setattr(
        runtime,
        "_run",
        lambda arguments, **_kwargs: commands.append(list(arguments))
        or subprocess.CompletedProcess(arguments, 0, "", ""),
    )

    assert runtime.stop("run-id", timeout_seconds=1) is expected
    assert ["rm", "mc-run-id"] in commands


@pytest.mark.parametrize(("exists_code", "raises"), [(1, False), (0, True), (125, True)])
def test_inspect_distinguishes_missing_container_from_podman_failure(
    monkeypatch, exists_code: int, raises: bool
) -> None:
    runtime = PodmanRuntime("podman")

    def fake_run(arguments, *, check=True, timeout=60):
        del check, timeout
        return_code = exists_code if arguments[:2] == ["container", "exists"] else 125
        return subprocess.CompletedProcess(arguments, return_code, "", "temporary failure")

    monkeypatch.setattr(runtime, "_run", fake_run)
    if raises:
        with pytest.raises(PodmanError, match="temporary failure"):
            runtime._inspect("run-id")
    else:
        assert runtime._inspect("run-id") is None
