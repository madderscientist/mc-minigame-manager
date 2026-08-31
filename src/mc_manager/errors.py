from typing import Any


class ManagerError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class ConflictError(ManagerError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(409, code, message)


class NotFoundError(ManagerError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(404, code, message)


class ValidationError(ManagerError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(422, code, message)
