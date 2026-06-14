from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
_pg_kwargs = (
    {}
    if _is_sqlite
    else {"pool_size": 10, "max_overflow": 20, "pool_recycle": 3600, "pool_pre_ping": True}
)
engine = create_engine(settings.database_url, **_pg_kwargs)

if _is_sqlite:
    # WAL + busy_timeout let readers/writers coexist instead of "database is locked".
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
