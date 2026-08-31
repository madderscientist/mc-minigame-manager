from alembic import command
from alembic.config import Config

from mc_manager.config import get_settings
from mc_manager.db import Database


def init_command() -> None:
    settings = get_settings()
    command.upgrade(Config("alembic.ini"), "head")
    Database(settings).initialize()
    print("Database, storage directories and port pool initialized.")
