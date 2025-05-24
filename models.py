from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
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
    auto_dial = Column(Boolean, default=False)  # Private feature flag
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

class AutodialCampaign(Base):
    __tablename__ = 'autodial_campaigns'
    
    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(BigInteger, ForeignKey('agents.telegram_id'))
    name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<AutodialCampaign(id={self.id}, name={self.name})>"

class AutodialResponse(Base):
    __tablename__ = 'autodial_responses'
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey('autodial_campaigns.id'))
    phone_number = Column(String)
    response_digit = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<AutodialResponse(campaign_id={self.campaign_id}, phone_number={self.phone_number}, response={self.response_digit})>"

class Call(Base):
    __tablename__ = 'calls'
    
    id = Column(Integer, primary_key=True)
    call_id = Column(String(255), unique=True, index=True)  # Our identifier for the call
    campaign_id = Column(Integer, ForeignKey('autodial_campaigns.id'), nullable=True)  # Campaign ID with proper FK
    sequence_number = Column(Integer, nullable=True)  # Position in the campaign sequence
    tracking_id = Column(String(50), nullable=True, index=True)  # Format: JKD1.{sequence_number}
    agent_telegram_id = Column(BigInteger, ForeignKey('agents.telegram_id'), nullable=True, index=True)  # TG ID who initiated the call
    target_number = Column(String(50))  # Number being called
    caller_id = Column(String(50))  # CallerID being used
    trunk = Column(String(50))  # Trunk being used
    uniqueid = Column(String(150), nullable=True, index=True)  # Asterisk Uniqueid
    channel = Column(String(150), nullable=True, index=True)  # Asterisk Channel
    action_id = Column(String(150), nullable=True)  # ActionID for originate
    status = Column(String(50), default='new')  # Call status
    dtmf_digits = Column(String(50), nullable=True)  # DTMF digits entered
    start_time = Column(DateTime, default=datetime.now)  # When call was initiated
    end_time = Column(DateTime, nullable=True)  # When call ended
    # JSON metadata for additional data
    call_metadata = Column(JSON, nullable=True)
    
    # Relationships
    campaign = relationship("AutodialCampaign", backref="calls")
    agent = relationship("Agent", backref="calls", foreign_keys=[agent_telegram_id])
    
    def __repr__(self):
        return f"<Call(call_id={self.call_id}, target={self.target_number}, status={self.status})>"
        
    @classmethod
    async def find_by_uniqueid(cls, session, uniqueid):
        """Find a call by Asterisk Uniqueid"""
        from sqlalchemy import select
        stmt = select(cls).where(cls.uniqueid == uniqueid)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
        
    @classmethod
    async def find_by_channel(cls, session, channel):
        """Find a call by Asterisk Channel name"""
        from sqlalchemy import select
        stmt = select(cls).where(cls.channel == channel)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
        
    @classmethod
    async def find_by_call_id(cls, session, call_id):
        """Find a call by our call_id"""
        from sqlalchemy import select
        stmt = select(cls).where(cls.call_id == call_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
        
    @classmethod
    async def find_latest_by_target(cls, session, target_number):
        """Find the most recent call to a specific target number"""
        from sqlalchemy import select, desc
        stmt = select(cls).where(cls.target_number == target_number).order_by(desc(cls.start_time)).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
        
    @classmethod
    async def find_latest_pending(cls, session):
        """Find the most recent call without a uniqueid or channel"""
        from sqlalchemy import select, desc, or_
        stmt = select(cls).where(
            or_(cls.uniqueid == None, cls.channel == None)
        ).order_by(desc(cls.start_time)).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
        
    @classmethod
    async def find_by_tracking_id(cls, session, tracking_id):
        """Find a call by tracking_id (e.g., JKD1.1)"""
        from sqlalchemy import select
        stmt = select(cls).where(cls.tracking_id == tracking_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()