from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os
from dotenv import load_dotenv
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
# Ensure the URL uses the asyncpg driver
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable not set.")
    # Consider raising an error or exiting if DB is essential
    engine = None
    async_session_maker = None
else:
    try:
        engine = create_async_engine(DATABASE_URL, echo=False) # Set echo=True for debugging SQL
        async_session_maker = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    except Exception as e:
        logger.error(f"Failed to create database engine: {e}")
        engine = None
        async_session_maker = None

Base = declarative_base()
# Remove Base.query as it's synchronous
# Base.query = db_session.query_property()

async def init_db():
    """Initialize the database asynchronously."""
    if not engine:
        logger.error("Database engine not initialized. Cannot create tables.")
        return
        
    # Import all models so they are registered with Base.metadata
    from models import Agent
    
    logger.info("Creating database tables...")
    try:
        async with engine.begin() as conn:
            # await conn.run_sync(Base.metadata.drop_all) # Optional: drop tables first
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Error creating database tables: {str(e)}")
        raise

@asynccontextmanager
async def get_session():
    """Provide a transactional scope around a series of operations."""
    if not async_session_maker:
        logger.error("Database session maker not initialized.")
        raise RuntimeError("Database session maker not initialized.")
        
    session = async_session_maker()
    try:
        yield session
        await session.commit() # Commit automatically on successful exit
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close() 