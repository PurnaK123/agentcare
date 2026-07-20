from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base
from app.seed import seed_database


@pytest.fixture
def db(tmp_path) -> Generator[Session, None, None]:
    settings = get_settings()
    settings.upload_dir = tmp_path / "uploads"
    settings.staging_dir = tmp_path / "staging"
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as session:
        seed_database(session)
        yield session
    engine.dispose()
