from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mc_manager.enums import (
    MapSourceType,
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


STRUCTURED_SERVER_PROPERTIES = {
    "allow-flight",
    "difficulty",
    "gamemode",
    "generate-structures",
    "hardcore",
    "level-seed",
    "level-type",
    "max-players",
    "pvp",
    "simulation-distance",
    "spawn-protection",
    "view-distance",
    "white-list",
}
RESERVED_SERVER_PROPERTIES = {
    "enable-command-block",
    "function-permission-level",
    "level-name",
    "op-permission-level",
    "server-port",
}


class ServerSettings(ApiModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    spawn_protection: int | None = Field(default=None, ge=0, le=10_000)
    gamemode: Literal["survival", "creative", "adventure", "spectator"] | None = None
    difficulty: Literal["peaceful", "easy", "normal", "hard"] | None = None
    hardcore: bool | None = None
    pvp: bool | None = None
    allow_flight: bool | None = None
    max_players: int | None = Field(default=None, ge=1, le=10_000)
    white_list: bool | None = None
    view_distance: int | None = Field(default=None, ge=3, le=32)
    simulation_distance: int | None = Field(default=None, ge=3, le=32)
    level_seed: str | None = Field(default=None, max_length=128)
    level_type: str | None = Field(default=None, min_length=1, max_length=128)
    generate_structures: bool | None = None
    custom: dict[str, str] = Field(default_factory=dict)

    @field_validator("level_seed", "level_type", mode="before")
    @classmethod
    def empty_world_setting_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_custom_properties(self) -> Self:
        for key, value in self.custom.items():
            normalized_key = key.strip().lower()
            if not normalized_key or len(key) > 128:
                raise ValueError("自定义属性名不能为空且不能超过 128 个字符")
            if any(
                character
                not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
                for character in key
            ):
                raise ValueError(f"自定义属性名无效: {key}")
            if normalized_key in STRUCTURED_SERVER_PROPERTIES:
                raise ValueError(f"自定义属性与结构化字段重复: {key}")
            if normalized_key in RESERVED_SERVER_PROPERTIES or normalized_key.startswith(
                "resource-pack"
            ):
                raise ValueError(f"不能覆盖系统托管属性: {key}")
            if len(value) > 1024 or any(character in value for character in "\r\n\0"):
                raise ValueError(f"自定义属性值无效: {key}")
        for field_name in ("level_seed", "level_type"):
            value = getattr(self, field_name)
            if value is not None and any(character in value for character in "\r\n\0"):
                raise ValueError(f"{field_name} 不能包含控制字符")
        return self


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
    source_type: MapSourceType
    mc_version: str
    data_version: int | None
    paper_build: str
    java_major: int
    created_at: datetime
    resource_pack: ResourcePackView | None
    server_settings: ServerSettings


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
    server_settings: ServerSettings


class CreateGameRequest(ApiModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    map_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    server_settings: ServerSettings | None = None


class GenerateMapRequest(ApiModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    mc_version: str = Field(min_length=1, max_length=32)
    paper_build: str | None = Field(default=None, min_length=1, max_length=64)
    paper_url: str | None = Field(default=None, max_length=2048)
    paper_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    server_settings: ServerSettings = Field(default_factory=ServerSettings)


class StartRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)
    port: int | None = Field(default=None, ge=1024, le=65535)


class StopRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    game_id: int = Field(gt=0)
    backup: bool = True


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


class PaperVersionView(ApiModel):
    version: str
    java_major: int


class ErrorBody(ApiModel):
    error: dict[str, Any]
