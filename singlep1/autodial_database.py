from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os
from dotenv import load_dotenv
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Load environment variables specifically from 'autodial.env'
load_dotenv(dotenv_path='autodial.env')

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

if not DATABASE_URL:
    logger.error("DATABASE_URL not found in autodial.env or is invalid.")
    engine = None
    async_session_maker = None
else:
    try:
        engine = create_async_engine(DATABASE_URL, echo=False) # Set echo=True for SQL debugging
        async_session_maker = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    except Exception as e:
        logger.error(f"Failed to create database engine: {e}")
        engine = None
        async_session_maker = None

Base = declarative_base()

async def init_db():
    """Initialize the database asynchronously."""
    if not engine:
        logger.error("Database engine not initialized. Cannot create tables.")
        return
        
    # Import models here to ensure they are registered with Base.metadata
    # This assumes autodial_models.py is in the same directory
    import autodial_models
    
    logger.info("Creating database tables for autodialer (if they don't exist)...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables checked/created successfully.")
    except Exception as e:
        logger.error(f"Error creating/checking database tables: {str(e)}")
        raise

@asynccontextmanager
async def get_session() -> AsyncSession:
    """Provide a transactional scope around a series of operations."""
    if not async_session_maker:
        logger.error("Database session maker not initialized.")
        raise RuntimeError("Database session maker not initialized.")
        
    session = async_session_maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
