import threading

from mc_manager.runtime.base import RuntimeBackend, RuntimeSpec


class FakeRuntime(RuntimeBackend):
    def __init__(self) -> None:
        self._instances: dict[str, RuntimeSpec] = {}
        self._lock = threading.Lock()

    def start(self, spec: RuntimeSpec) -> str:
        with self._lock:
            self._instances.setdefault(spec.run_id, spec)
        return f"fake-{spec.run_id}"

    def wait_ready(self, run_id: str, port: int, timeout_seconds: int) -> bool:
        del port, timeout_seconds
        return self.exists(run_id)

    def stop(self, run_id: str, timeout_seconds: int = 120) -> bool:
        del timeout_seconds
        with self._lock:
            self._instances.pop(run_id, None)
        return True

    def exists(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._instances

    def managed_run_ids(self) -> set[str]:
        with self._lock:
            return set(self._instances)
