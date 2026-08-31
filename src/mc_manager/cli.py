from pathlib import Path

from alembic import command
from alembic.config import Config

from mc_manager.config import get_settings
from mc_manager.db import Database


def init_command() -> None:
    settings = get_settings()
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent / "migrations"),
    )
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    Database(settings).initialize()
    print("Database, storage directories and port pool initialized.")
