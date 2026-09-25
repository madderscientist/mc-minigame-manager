import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from mc_manager.errors import ManagerError, ValidationError

MAX_LOG_BYTES = 256 * 1024


@dataclass
class GameLogSnapshot:
    lines: list[str]
    available: bool
    truncated: bool


def read_game_logs(root: Path, relative_path: str, tail: int) -> GameLogSnapshot:
    """Read a bounded tail without following symlinks in the game directory tree."""
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValidationError("unsafe_path", "游戏日志路径无效")

    try:
        with ExitStack() as stack:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for part in (*path.parts, "logs"):
                directory = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                stack.callback(os.close, directory)
            descriptor = os.open(
                "latest.log",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValidationError("invalid_log_file", "游戏日志不是普通文件")
            offset = max(0, info.st_size - MAX_LOG_BYTES)
            # Include the preceding byte to distinguish a complete first line.
            start = max(0, offset - 1)
            os.lseek(descriptor, start, os.SEEK_SET)
            data = os.read(descriptor, min(info.st_size - start, MAX_LOG_BYTES + 1))
    except FileNotFoundError:
        return GameLogSnapshot(lines=[], available=False, truncated=False)
    except OSError as exc:
        raise ManagerError(
            500, "game_logs_unreadable", "无法读取游戏日志: 请检查日志文件及服务端读取权限"
        ) from exc

    if offset:
        boundary = data[:1] == b"\n"
        data = data[1:]
        if not boundary and b"\n" in data:
            data = data.split(b"\n", 1)[1]
    lines = data.decode("utf-8", errors="replace").splitlines()
    return GameLogSnapshot(
        lines=lines[-tail:], available=True, truncated=offset > 0 or len(lines) > tail
    )