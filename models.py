from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, ForeignKey
from datetime import datetime
from database import Base

class Agent(Base):
    __tablename__ = 'agents'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    phone_number = Column(String, unique=True)
    caller_id = Column(String)
    autodial_caller_id = Column(String)
    route = Column(String(1))  # 'M', 'R', 'B' for manual calls
    autodial_trunk = Column(String(3)) # 'one' or 'two' for autodial calls
    username = Column(String)
    is_authorized = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Agent(telegram_id={self.telegram_id}, username={self.username})>"

class CallerIDHistory(Base):
    __tablename__ = 'caller_id_history'
    
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey('agents.id'))
    old_caller_id = Column(String)
    new_caller_id = Column(String)
    changed_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<CallerIDHistory(agent_id={self.agent_id}, new_caller_id={self.new_caller_id})>" 