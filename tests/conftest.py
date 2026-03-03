"""Shared test fixtures for Spartacus Arena tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake-key")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-elevenlabs-key")
os.environ.setdefault("ELEVENLABS_VOICE_ID", "test-voice-id")
os.environ.setdefault("OUTPUT_ROOT", str(Path(__file__).parent / "_test_output"))

from database.models import Base


@pytest.fixture
def db_engine():
    """Create a fresh in-memory SQLite database for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Provide a transactional DB session that rolls back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    Session_ = sessionmaker(bind=connection)
    session = Session_()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def clean_test_output():
    """Ensure test output directory exists and is clean."""
    out = Path(__file__).parent / "_test_output"
    out.mkdir(parents=True, exist_ok=True)
    yield
