"""Database connection setup (SQLAlchemy + SQLite).

- engine: the connection to the database file
- SessionLocal: creates a short-lived session per request
- get_db: FastAPI dependency that opens a session and ALWAYS closes it
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401  (registers the tables)
    Base.metadata.create_all(bind=engine)
