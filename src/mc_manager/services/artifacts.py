import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from mc_manager.errors import ValidationError
from mc_manager.services.versions import required_java_major


@dataclass(frozen=True, slots=True)
class PaperArtifact:
    url: str
    sha256: str


RELEASE_VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


def _fetch_papermc_json(endpoint: str, user_agent: str, subject: str) -> Any:
    try:
        response = httpx.get(
            endpoint,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=30,
            follow_redirects=False,
        )
        if response.is_redirect:
            raise ValidationError("papermc_redirect_rejected", "拒绝 PaperMC API 重定向")
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise ValidationError(
            "papermc_unavailable", f"暂时无法查询 PaperMC {subject}"
        ) from error
    try:
        return response.json()
    except ValueError as error:
        raise ValidationError("papermc_response_invalid", "PaperMC 返回格式无效") from error


def supported_paper_versions(user_agent: str) -> list[tuple[str, int]]:
    payload = _fetch_papermc_json(
        "https://fill.papermc.io/v3/projects/paper", user_agent, "版本"
    )
    versions = payload.get("versions") if isinstance(payload, dict) else None
    if not isinstance(versions, dict):
        raise ValidationError("papermc_response_invalid", "PaperMC 返回格式无效")
    supported: list[tuple[str, int]] = []
    for releases in versions.values():
        if not isinstance(releases, list):
            raise ValidationError("papermc_response_invalid", "PaperMC 返回格式无效")
        for release in releases:
            if not isinstance(release, str) or RELEASE_VERSION.fullmatch(release) is None:
                continue
            try:
                java_major = required_java_major(release)
            except ValueError:
                continue
            supported.append((release, java_major))
    return list(dict.fromkeys(supported))


def _fetch_paper_builds(mc_version: str, user_agent: str) -> list[Any]:
    endpoint = f"https://fill.papermc.io/v3/projects/paper/versions/{mc_version}/builds"
    payload = _fetch_papermc_json(endpoint, user_agent, "build")
    if not isinstance(payload, list):
        raise ValidationError("papermc_response_invalid", "PaperMC 返回格式无效")
    return payload


def latest_stable_paper_build(mc_version: str, *, user_agent: str) -> str:
    builds = _fetch_paper_builds(mc_version, user_agent)
    stable_ids = [
        int(item["id"])
        for item in builds
        if isinstance(item, dict)
        and item.get("channel") == "STABLE"
        and str(item.get("id", "")).isdigit()
        and isinstance(item.get("downloads"), dict)
        and "server:default" in item["downloads"]
    ]
    if not stable_ids:
        raise ValidationError(
            "paper_build_not_found", "该 Minecraft 版本没有可用的稳定 Paper build"
        )
    return str(max(stable_ids))


class ArtifactManager:
    def __init__(
        self,
        root: Path,
        *,
        user_agent: str,
        allow_unstable: bool = False,
        allowed_hosts: set[str] | None = None,
        max_artifact_bytes: int = 512 * 1024**2,
    ) -> None:
        self.root = root
        self.user_agent = user_agent
        self.allow_unstable = allow_unstable
        self.allowed_hosts = allowed_hosts or {"fill.papermc.io", "fill-data.papermc.io"}
        self.max_artifact_bytes = max_artifact_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_paper(self, mc_version: str, paper_build: str) -> PaperArtifact:
        payload = _fetch_paper_builds(mc_version, self.user_agent)
        build = next(
            (
                item
                for item in payload
                if isinstance(item, dict) and str(item.get("id")) == str(paper_build)
            ),
            None,
        )
        if build is None:
            raise ValidationError("paper_build_not_found", "找不到指定的 Paper build")
        if not self.allow_unstable and build.get("channel") != "STABLE":
            raise ValidationError("paper_build_unstable", "指定 Paper build 不是稳定版本")
        try:
            download = build["downloads"]["server:default"]
            url = str(download["url"])
            sha256 = str(download["checksums"]["sha256"])
        except (KeyError, TypeError) as error:
            raise ValidationError("papermc_response_invalid", "Paper 下载信息不完整") from error
        return PaperArtifact(url=url, sha256=sha256)

    def ensure_paper(self, url: str | None, sha256: str | None) -> Path:
        if not url or not sha256 or len(sha256) != 64:
            raise ValidationError(
                "paper_artifact_missing", "Docker 运行模式需要固定的 Paper URL 和 SHA-256"
            )
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValidationError("paper_url_invalid", "Paper 下载地址必须使用 HTTPS")
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise ValidationError("paper_host_not_allowed", "Paper 下载主机不在允许列表中")
        destination = self.root / "paper" / f"{sha256.lower()}.jar"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if self._sha256(destination) == sha256.lower():
                return destination
            destination.unlink()

        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with httpx.stream(
                "GET",
                url,
                headers={"User-Agent": self.user_agent},
                timeout=120,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    raise ValidationError("paper_redirect_rejected", "拒绝 Paper 下载重定向")
                response.raise_for_status()
                written = 0
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > self.max_artifact_bytes:
                            raise ValidationError("paper_too_large", "Paper JAR 超过大小限制")
                        output.write(chunk)
            if self._sha256(temporary) != sha256.lower():
                raise ValidationError("paper_checksum_mismatch", "Paper JAR 校验失败")
            os.replace(temporary, destination)
            return destination
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def accept_eula(game_path: Path) -> None:
        target = game_path / "eula.txt"
        temporary = game_path / f".eula-{uuid.uuid4().hex}.tmp"
        temporary.write_text("eula=true\n", encoding="utf-8")
        os.replace(temporary, target)
