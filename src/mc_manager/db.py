from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from mc_manager.config import Settings
from mc_manager.enums import PortState
from mc_manager.models import Base, PortLease


class Database:
    def __init__(self, settings: Settings) -> None:
        connect_args = (
            {"check_same_thread": False}
            if settings.database_url.startswith("sqlite")
            else {}
        )
        self.engine = create_engine(settings.database_url, connect_args=connect_args)
        if settings.database_url.startswith("sqlite"):
            self._configure_sqlite(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )
        self.settings = settings

    @staticmethod
    def _configure_sqlite(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_pragmas(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    def initialize(self) -> None:
        if self.settings.database_url.startswith("sqlite"):
            database_name = make_url(self.settings.database_url).database
            if database_name and database_name != ":memory:":
                Path(database_name).parent.mkdir(parents=True, exist_ok=True)
        if self.settings.auto_create_schema:
            Base.metadata.create_all(self.engine)
        self.ensure_port_pool()

    def ensure_port_pool(self) -> None:
        with self.session_factory.begin() as session:
            ports = [
                {"port": port, "state": PortState.FREE}
                for port in range(self.settings.port_min, self.settings.port_max + 1)
            ]
            if ports:
                session.execute(
                    sqlite_insert(PortLease)
                    .values(ports)
                    .on_conflict_do_nothing(index_elements=["port"])
                )

    def session_dependency(self) -> Generator[Session, None, None]:
        with self.session_factory() as session:
            try:
                yield session
            finally:
                session.close()
