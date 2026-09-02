from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mc_manager.enums import (
    ObservedState,
    PortState,
    ResourceState,
    TaskStatus,
    TaskType,
)


class ApiModel(BaseModel):
    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def attach_utc_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value


class BackupView(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    backup_id: str
    reason: str
    clean_shutdown: bool
    size_bytes: int
    sha256: str
    created_at: datetime


class ResourcePackView(ApiModel):
    filename: str
    sha1: str
    sha256: str
    size_bytes: int
    pack_format: int
    required: bool
    prompt: str | None
    url: str


class MapView(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    map_id: int
    state: ResourceState
    name: str
    mc_version: str
    data_version: int | None
    paper_build: str
    java_major: int
    created_at: datetime
    resource_pack: ResourcePackView | None


class GameView(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    game_id: int
    map_id: int
    map_name: str
    mc_version: str
    paper_build: str
    java_major: int
    state: ResourceState
    name: str
    created_at: datetime
    last_played_at: datetime | None
    runtime_state: ObservedState | None = None
    port: int | None = None
    public_address: str | None = None
    backups: list[BackupView] = Field(default_factory=list)


class CreateGameRequest(ApiModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    map_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=255)


class StartRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)
    port: int | None = Field(default=None, ge=1024, le=65535)


class StopRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)


class LoadRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)
    backup_id: str = Field(min_length=10, max_length=32)


class TaskView(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    type: TaskType
    status: TaskStatus
    step: str
    map_id: int | None
    game_id: int | None
    backup_id: str | None
    requested_port: int | None
    progress: float
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class CreateGameAccepted(ApiModel):
    task_id: str
    game_id: int
    map_id: int
    status: TaskStatus = TaskStatus.PENDING


class StartAccepted(ApiModel):
    task_id: str
    game_id: int
    port: int
    status: TaskStatus = TaskStatus.PENDING


class StopAccepted(ApiModel):
    task_id: str
    game_id: int
    status: TaskStatus = TaskStatus.PENDING


class DeleteGameAccepted(ApiModel):
    task_id: str
    game_id: int
    status: TaskStatus = TaskStatus.PENDING


class LoadAccepted(ApiModel):
    task_id: str
    game_id: int
    backup_id: str
    status: TaskStatus = TaskStatus.PENDING


class RunningGameView(ApiModel):
    game_id: int
    game_name: str
    mc_version: str
    last_played_at: datetime | None
    observed_state: ObservedState
    port: int
    public_address: str | None
    last_error: str | None


class PortView(ApiModel):
    port: int
    state: PortState
    game_id: int | None


class StatusView(ApiModel):
    running_games: list[RunningGameView]
    tasks: list[TaskView]
    ports: list[PortView]


class UploadResult(ApiModel):
    map_id: int
    name: str
    mc_version: str


class ErrorBody(ApiModel):
    error: dict[str, Any]
