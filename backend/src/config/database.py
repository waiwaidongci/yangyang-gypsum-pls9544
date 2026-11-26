from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .settings import DATABASE_URL


class Base(DeclarativeBase):
    pass


if DATABASE_URL.startswith('sqlite'):
    # Share one in-memory database across sessions/threads (tests/TestClient)
    # and permit the connection to move between worker threads for file SQLite.
    memory = DATABASE_URL in ('sqlite://', 'sqlite:///:memory:') or ':memory:' in DATABASE_URL
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={'check_same_thread': False},
        **({'poolclass': StaticPool} if memory else {}),
    )
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine, expire_on_commit=False)


def session_scope():
    with Session() as session:
        yield session
