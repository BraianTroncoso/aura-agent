from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

# asyncpg doesn't accept libpq query params like ?sslmode=require / ?channel_binding
# (which Neon/Supabase URLs include). Strip them and translate to a connect_args ssl
# flag so a copy-pasted hosted-Postgres URL connects without manual edits.
_connect_args: dict = {}
if "+asyncpg" in _async_url:
    _parts = urlsplit(_async_url)
    _q = dict(parse_qsl(_parts.query))
    _sslmode = _q.pop("sslmode", None)
    _q.pop("channel_binding", None)
    _async_url = urlunsplit(
        (_parts.scheme, _parts.netloc, _parts.path, urlencode(_q), _parts.fragment)
    )
    if _sslmode and _sslmode != "disable":
        _connect_args["ssl"] = True

_pg_kwargs = (
    {"pool_size": 10, "max_overflow": 0, "pool_pre_ping": True, "pool_recycle": 3600,
     "connect_args": _connect_args}
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
