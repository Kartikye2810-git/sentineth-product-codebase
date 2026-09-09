from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.settings import get_settings


settings = get_settings()
database_url = settings.database_url.get_secret_value()

# A connection attempt or pool wait that never returns turns one unreachable
# database into a hung API process. SQLite accepts neither option.
postgres_timeouts = (
    {"pool_timeout": 3, "connect_args": {"connect_timeout": 3}}
    if database_url.startswith("postgresql")
    else {}
)

engine = create_engine(
    database_url,
    # Statement logging is opt-in. Leaving echo on unconditionally logs
    # every query - including document content - and is slow.
    echo=settings.sql_echo,
    pool_pre_ping=True,
    # Bound parameters can be document text or a password hash; keep them
    # out of SQLAlchemy's own error messages.
    hide_parameters=True,
    **postgres_timeouts,
)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()