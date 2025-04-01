from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from .database import Base

class Agent(Base):
    """Agent model for storing user information."""
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String, index=True)
    phone_number = Column(String)
    route = Column(String)  # M, R, or B
    caller_id = Column(String)  # Optional custom caller ID
    is_authorized = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now()) 