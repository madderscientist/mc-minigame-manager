from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mc_manager.enums import (
    ObservedState,
    PortState,
    ResourceState,
    TaskStatus,
    TaskType,
)


class BackupView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    backup_id: str
    reason: str
    clean_shutdown: bool
    size_bytes: int
    sha256: str
    created_at: datetime


class MapView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    map_id: int
    state: ResourceState
    name: str
    mc_version: str
    data_version: int | None
    paper_build: str
    java_major: int
    created_at: datetime


class GameView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    game_id: int
    map_id: int
    state: ResourceState
    name: str
    created_at: datetime
    last_played_at: datetime | None
    runtime_state: ObservedState | None = None
    port: int | None = None
    backups: list[BackupView] = Field(
        default_factory=list, validation_alias="retained_backups"
    )


class CreateGameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    map_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=255)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)
    port: int | None = Field(default=None, ge=1024, le=65535)


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)


class LoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)
    backup_id: str = Field(min_length=10, max_length=32)


class TaskView(BaseModel):
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


class CreateGameAccepted(BaseModel):
    task_id: str
    game_id: int
    map_id: int
    status: TaskStatus = TaskStatus.PENDING


class StartAccepted(BaseModel):
    task_id: str
    game_id: int
    port: int
    status: TaskStatus = TaskStatus.PENDING


class StopAccepted(BaseModel):
    task_id: str
    game_id: int
    status: TaskStatus = TaskStatus.PENDING


class DeleteGameAccepted(BaseModel):
    task_id: str
    game_id: int
    status: TaskStatus = TaskStatus.PENDING


class LoadAccepted(BaseModel):
    task_id: str
    game_id: int
    backup_id: str
    status: TaskStatus = TaskStatus.PENDING


class RunningGameView(BaseModel):
    game_id: int
    observed_state: ObservedState
    port: int
    last_error: str | None


class PortView(BaseModel):
    port: int
    state: PortState
    game_id: int | None


class StatusView(BaseModel):
    running_games: list[RunningGameView]
    tasks: list[TaskView]
    ports: list[PortView]


class UploadResult(BaseModel):
    map_id: int
    name: str
    mc_version: str
    resources: list[str]


class ErrorBody(BaseModel):
    error: dict[str, Any]
