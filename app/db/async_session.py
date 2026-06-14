from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Convierte postgresql:// → postgresql+asyncpg:// (no-op si ya está convertida)
_db = settings.database_url
if _db.startswith("sqlite"):
    _async_url = _db.replace("sqlite://", "sqlite+aiosqlite://", 1)
elif "+asyncpg" not in _db:
    _async_url = _db.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )
else:
    _async_url = _db

_pg_kwargs = (
    {"pool_size": 10, "max_overflow": 0, "pool_pre_ping": True, "pool_recycle": 3600}
    if "sqlite" not in _async_url
    else {}
)
async_engine = create_async_engine(_async_url, echo=False, **_pg_kwargs)

if "sqlite" in _async_url:
    from sqlalchemy import event

    @event.listens_for(async_engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
