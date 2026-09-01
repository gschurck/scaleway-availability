from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._ensure_sqlite_directory()
        self.engine = create_async_engine(
            settings.database_url,
            echo=False,
            connect_args={"timeout": 30} if settings.database_url.startswith("sqlite") else {},
        )
        if settings.database_url.startswith("sqlite"):
            self._configure_sqlite(self.engine)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    def _ensure_sqlite_directory(self) -> None:
        path = self.settings.sqlite_path
        if path and str(path) != ":memory:":
            parent = path.parent if path.is_absolute() else Path.cwd() / path.parent
            parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _configure_sqlite(engine: AsyncEngine) -> None:
        @event.listens_for(engine.sync_engine, "connect")
        def set_pragmas(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async def create_schema(self) -> None:
        from app import models  # noqa: F401

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session
