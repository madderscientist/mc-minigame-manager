from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from mc_manager.enums import (
    DesiredState,
    ObservedState,
    PortState,
    ResourceState,
    TaskStatus,
    TaskType,
)

EnumT = TypeVar("EnumT", bound=Enum)


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum_type(enum_class: type[EnumT], name: str) -> SAEnum:
    return SAEnum(
        enum_class,
        name=name,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    pass


class MapRecord(Base):
    __tablename__ = "maps"

    map_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[ResourceState] = mapped_column(
        enum_type(ResourceState, "resource_state"),
        default=ResourceState.PREPARING,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mc_version: Mapped[str] = mapped_column(String(32), nullable=False)
    data_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper_build: Mapped[str] = mapped_column(String(64), nullable=False)
    java_major: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relative_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    import_idempotency_key: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    import_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    games: Mapped[list[GameRecord]] = relationship(
        back_populates="map", passive_deletes=True
    )

    @property
    def resource_pack(self) -> dict[str, Any] | None:
        value = self.extra_metadata.get("resource_pack")
        return value if isinstance(value, dict) else None


class GameRecord(Base):
    __tablename__ = "games"

    game_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(
        ForeignKey("maps.map_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state: Mapped[ResourceState] = mapped_column(
        enum_type(ResourceState, "resource_state"),
        default=ResourceState.PREPARING,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_lock_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    map: Mapped[MapRecord] = relationship(back_populates="games", lazy="joined")
    backups: Mapped[list[BackupRecord]] = relationship(
        back_populates="game", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def retained_backups(self) -> list[BackupRecord]:
        return [backup for backup in self.backups if backup.retained]


class BackupRecord(Base):
    __tablename__ = "backups"
    __table_args__ = (
        UniqueConstraint("game_id", "backup_id", name="uq_backup_game_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False, index=True
    )
    backup_id: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    clean_shutdown: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retained: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    game: Mapped[GameRecord] = relationship(back_populates="backups")


class RunRecord(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.game_id", ondelete="CASCADE"), nullable=False, index=True
    )
    port: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    desired_state: Mapped[DesiredState] = mapped_column(
        enum_type(DesiredState, "desired_state"), nullable=False
    )
    observed_state: Mapped[ObservedState] = mapped_column(
        enum_type(ObservedState, "observed_state"), nullable=False
    )
    container_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game: Mapped[GameRecord] = relationship(lazy="joined")


Index(
    "uq_one_live_run_per_game",
    RunRecord.game_id,
    unique=True,
    sqlite_where=text(
        "observed_state IN ('preparing','starting','ready','stopping','backing_up','unknown')"
    ),
)


class PortLease(Base):
    __tablename__ = "port_leases"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[PortState] = mapped_column(
        enum_type(PortState, "port_state"), default=PortState.FREE, nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.run_id", ondelete="SET NULL"), unique=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TaskRecord(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[TaskType] = mapped_column(
        enum_type(TaskType, "task_type"), nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        enum_type(TaskStatus, "task_status"), nullable=False, index=True
    )
    step: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    map_id: Mapped[int | None] = mapped_column(
        ForeignKey("maps.map_id", ondelete="SET NULL"), index=True
    )
    game_id: Mapped[int | None] = mapped_column(
        ForeignKey("games.game_id", ondelete="SET NULL"), index=True
    )
    backup_id: Mapped[str | None] = mapped_column(String(32))
    requested_port: Mapped[int | None] = mapped_column(Integer)
    backup_requested: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.run_id", ondelete="SET NULL"), index=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_one_pending_stop_per_run",
    TaskRecord.run_id,
    unique=True,
    sqlite_where=text("type = 'stop' AND status IN ('pending','running')"),
)
