from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
import os

# Get database URL from environment variable
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///bot.db')

# Create engine
engine = create_engine(DATABASE_URL)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()

@contextmanager
def get_db_session() -> Session:
    """Get a database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close() 