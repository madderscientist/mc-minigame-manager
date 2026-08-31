from mc_manager.config import Settings
from mc_manager.runtime.base import RuntimeBackend
from mc_manager.runtime.docker_backend import DockerRuntime
from mc_manager.runtime.fake import FakeRuntime
from mc_manager.runtime.podman_backend import PodmanRuntime


def build_runtime(settings: Settings) -> RuntimeBackend:
    if settings.runtime_backend == "fake":
        return FakeRuntime()
    if settings.runtime_backend == "docker":
        return DockerRuntime(settings.docker_base_url, cpus=settings.container_cpus)
    if settings.runtime_backend == "podman":
        return PodmanRuntime(
            settings.podman_binary,
            cpus=settings.container_cpus,
            pull_timeout_seconds=settings.container_pull_timeout_seconds,
        )
    raise ValueError(f"Unsupported runtime backend: {settings.runtime_backend}")


__all__ = ["RuntimeBackend", "build_runtime"]
