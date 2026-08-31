import json
from functools import cached_property, lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    max_upload_bytes: int = Field(default=2 * 1024**3, ge=1)
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
        '"21":"eclipse-temurin:21-jre"}'
    )
    container_memory: str = "2g"
    container_cpus: float = Field(default=2.0, gt=0)

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
