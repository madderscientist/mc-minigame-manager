import json
import re
from functools import cached_property, lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Self, cast
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def _parse_default_operators(value: str) -> dict[str, str]:
    try:
        payload: object = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("MC_DEFAULT_OPERATORS_JSON must be a JSON object") from error
    if not isinstance(payload, dict):
        raise ValueError("MC_DEFAULT_OPERATORS_JSON must be a JSON object")
    operators: dict[str, str] = {}
    names: set[str] = set()
    for raw_name, raw_uuid in cast(dict[object, object], payload).items():
        if not isinstance(raw_name, str) or PLAYER_NAME.fullmatch(raw_name) is None:
            raise ValueError("MC_DEFAULT_OPERATORS_JSON contains an invalid player name")
        if not isinstance(raw_uuid, str):
            raise ValueError("MC_DEFAULT_OPERATORS_JSON contains a non-string UUID")
        try:
            normalized_uuid = str(UUID(raw_uuid))
        except ValueError as error:
            raise ValueError("MC_DEFAULT_OPERATORS_JSON contains an invalid UUID") from error
        normalized_name = raw_name.casefold()
        if normalized_uuid in operators or normalized_name in names:
            raise ValueError("MC_DEFAULT_OPERATORS_JSON contains duplicate players")
        operators[normalized_uuid] = raw_name
        names.add(normalized_name)
    return operators


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./var/mc-manager.db"
    auto_create_schema: bool = True
    storage_root: Path = Path("./var/storage")
    port_min: int = Field(default=30000, ge=1024, le=65535)
    port_max: int = Field(default=30099, ge=1024, le=65535)
    public_game_host: str | None = None
    public_game_port_min: int | None = Field(default=None, ge=1, le=65535)
    backup_limit: int = Field(default=10, ge=1, le=1000)
    runtime_backend: str = "fake"
    api_token: SecretStr | None = None
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    task_lease_seconds: int = Field(default=120, ge=10)
    start_timeout_seconds: int = Field(default=180, ge=10)
    papermc_user_agent: str = "mc-minigame-manager/0.2.0 (ops@example.com)"
    allow_unstable_paper: bool = False
    paper_allowed_hosts: str = "fill.papermc.io,fill-data.papermc.io"
    max_artifact_bytes: int = Field(default=512 * 1024**2, ge=1)
    default_operators_json: str = "{}"

    max_upload_bytes: int = Field(default=2 * 1024**3, ge=1)
    max_upload_overhead_bytes: int = Field(default=16 * 1024**2, ge=1024)
    max_upload_sessions: int = Field(default=8, ge=1, le=1024)
    max_upload_reserved_bytes: int = Field(default=8 * 1024**3, ge=1)
    resource_pack_base_url: str | None = None
    max_resource_pack_bytes: int = Field(
        default=250 * 1024**2, ge=1, le=250 * 1024**2
    )
    max_extracted_bytes: int = Field(default=8 * 1024**3, ge=1)
    max_archive_files: int = Field(default=100_000, ge=1)
    max_single_file_bytes: int = Field(default=2 * 1024**3, ge=1)
    max_compression_ratio: float = Field(default=200.0, ge=1)

    docker_base_url: str = "unix:///var/run/docker.sock"
    podman_binary: str = "/usr/bin/podman"
    container_pull_timeout_seconds: int = Field(default=900, ge=30)
    java_images_json: str = (
        '{"8":"eclipse-temurin:8-jre","11":"eclipse-temurin:11-jre",'
        '"16":"eclipse-temurin:16-jre","17":"eclipse-temurin:17-jre",'
        '"21":"eclipse-temurin:21-jre","25":"eclipse-temurin:25-jre"}'
    )
    container_memory: str = "2g"
    container_cpus: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def validate_port_range(self) -> Self:
        if self.port_min > self.port_max:
            raise ValueError("MC_PORT_MIN must be less than or equal to MC_PORT_MAX")
        if self.max_upload_reserved_bytes < self.max_upload_bytes:
            raise ValueError(
                "MC_MAX_UPLOAD_RESERVED_BYTES must be at least MC_MAX_UPLOAD_BYTES"
            )
        if (self.public_game_host is None) != (self.public_game_port_min is None):
            raise ValueError(
                "MC_PUBLIC_GAME_HOST and MC_PUBLIC_GAME_PORT_MIN must be configured together"
            )
        if self.public_game_port_min is not None:
            public_port_max = self.public_game_port_min + self.port_max - self.port_min
            if public_port_max > 65535:
                raise ValueError("public game port range must not exceed 65535")
        return self

    @field_validator("public_game_host")
    @classmethod
    def validate_public_game_host(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        host = value.strip()
        if "://" in host or any(character.isspace() for character in host):
            raise ValueError("MC_PUBLIC_GAME_HOST must be a hostname or IP without a scheme")
        return host

    def public_game_address(self, local_port: int) -> str | None:
        if self.public_game_host is None or self.public_game_port_min is None:
            return None
        if not self.port_min <= local_port <= self.port_max:
            return None
        public_port = self.public_game_port_min + local_port - self.port_min
        host = (
            f"[{self.public_game_host}]"
            if ":" in self.public_game_host
            else self.public_game_host
        )
        return f"{host}:{public_port}"

    @field_validator("resource_pack_base_url")
    @classmethod
    def validate_resource_pack_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MC_RESOURCE_PACK_BASE_URL must be a public HTTP(S) base URL")
        hostname = parsed.hostname or ""
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            raise ValueError("MC_RESOURCE_PACK_BASE_URL must not use a local hostname")
        try:
            address = ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError(
                    "MC_RESOURCE_PACK_BASE_URL must not use a private or local IP address"
                )
        return normalized

    @field_validator("default_operators_json")
    @classmethod
    def validate_default_operators_json(cls, value: str) -> str:
        _parse_default_operators(value)
        return value

    @cached_property
    def java_images(self) -> dict[int, str]:
        parsed = json.loads(self.java_images_json)
        if not isinstance(parsed, dict):
            raise ValueError("MC_JAVA_IMAGES_JSON must be a JSON object")
        return {int(key): str(value) for key, value in parsed.items()}

    @cached_property
    def allowed_paper_hosts(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.paper_allowed_hosts.split(",")
            if host.strip()
        }

    @cached_property
    def default_operators(self) -> dict[str, str]:
        return _parse_default_operators(self.default_operators_json)

    @property
    def map_root(self) -> Path:
        return self.storage_root / "maps"

    @property
    def game_root(self) -> Path:
        return self.storage_root / "games"

    @property
    def backup_root(self) -> Path:
        return self.storage_root / "backups"

    @property
    def artifact_root(self) -> Path:
        return self.storage_root / "artifacts"

    @property
    def upload_root(self) -> Path:
        return self.storage_root / "uploads"

    @property
    def staging_root(self) -> Path:
        return self.storage_root / ".staging"

    @property
    def api_staging_root(self) -> Path:
        return self.staging_root / "api"

    @property
    def worker_staging_root(self) -> Path:
        return self.staging_root / "worker"

    def ensure_directories(self) -> None:
        for path in (
            self.map_root,
            self.game_root,
            self.backup_root,
            self.artifact_root,
            self.upload_root,
            self.api_staging_root,
            self.worker_staging_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
