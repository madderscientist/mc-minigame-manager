from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    run_id: str
    container_name: str
    game_path: Path
    port: int
    image: str
    java_memory: str
    paper_jar: Path | None = None


class RuntimeBackend(ABC):
    @abstractmethod
    def start(self, spec: RuntimeSpec) -> str:
        """Start an instance idempotently and return its runtime/container ID."""

    @abstractmethod
    def wait_ready(self, run_id: str, port: int, timeout_seconds: int) -> bool:
        """Wait until Paper is ready to accept players."""

    @abstractmethod
    def stop(self, run_id: str, timeout_seconds: int = 120) -> bool:
        """Stop and remove an instance. Return whether shutdown was graceful."""

    @abstractmethod
    def exists(self, run_id: str) -> bool:
        """Return whether the managed runtime exists."""

    @abstractmethod
    def managed_run_ids(self) -> set[str]:
        """List run IDs owned by this manager."""
