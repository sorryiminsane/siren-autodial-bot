from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime

# This assumes autodial_database.py is in the same directory and defines Base
from autodial_database import Base

class Agent(Base):
    __tablename__ = 'autodial_agents' # Potentially a separate table for this bot's agents
    
    # Using telegram_id as primary key for simplicity if agents are only identified by this
    telegram_id = Column(BigInteger, primary_key=True, autoincrement=False)
    username = Column(String, nullable=True)
    is_authorized = Column(Boolean, default=False) # General authorization for the bot
    auto_dial_enabled = Column(Boolean, default=False)  # Specific permission for autodialing
    
    # Agent-specific autodial settings
    autodial_caller_id = Column(String, nullable=True)
    autodial_trunk = Column(String(50), nullable=True) # e.g., 'trunk-A', 'premium-route'
    max_concurrent_calls_override = Column(Integer, nullable=True) # Agent specific concurrency

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Agent(telegram_id={self.telegram_id}, username={self.username}, auto_dial_enabled={self.auto_dial_enabled})>"

class AutodialCampaign(Base):
    __tablename__ = 'autodial_campaigns'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, index=True)
    agent_telegram_id = Column(BigInteger, ForeignKey(f'{Agent.__tablename__}.telegram_id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    status = Column(String(50), default='pending', index=True) # e.g., pending, active, paused, completed, cancelled, failed
    
    # Store the list of numbers as JSON. For very large lists, a separate table might be better.
    phone_numbers_json = Column(JSON, nullable=False)
    # Configuration for this campaign (e.g., caller_id, trunk_context, retries)
    campaign_config_json = Column(JSON, nullable=True)
    
    # Summary/Progress fields
    total_numbers = Column(Integer, default=0)
    processed_numbers = Column(Integer, default=0)
    successful_calls = Column(Integer, default=0)
    failed_calls = Column(Integer, default=0)

    agent = relationship("Agent", backref="autodial_campaigns")

    def __repr__(self):
        return f"<AutodialCampaign(id={self.id}, name='{self.name}', status='{self.status}')>"

class AutodialCall(Base):
    __tablename__ = 'autodial_calls'

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey(f'{AutodialCampaign.__tablename__}.id'), nullable=False, index=True)
    phone_number = Column(String(50), nullable=False, index=True)
    status = Column(String(50), default='queued', index=True)  # queued, dialing, answered, no_answer, busy, failed, completed
    attempt_count = Column(Integer, default=0, index=True)
    last_attempt_time = Column(DateTime, nullable=True, index=True)
    
    # Call tracking and identification
    call_id = Column(String(150), nullable=True, unique=True, index=True)  # Unique call identifier
    tracking_id = Column(String(150), nullable=True, index=True)  # External tracking ID (e.g., JKD1.1)
    sequence_number = Column(Integer, nullable=True, index=True)  # Sequence in the campaign
    
    # Asterisk related info
    uniqueid = Column(String(150), nullable=True, index=True)  # Asterisk Uniqueid for the call leg
    channel = Column(String(150), nullable=True, index=True)  # Asterisk Channel
    action_id = Column(String(150), nullable=True, index=True)  # AMI ActionID used for origination

    # Call outcome details
    response_digit = Column(String(10), nullable=True, index=True)  # DTMF response if any
    call_duration_seconds = Column(Integer, nullable=True)  # In seconds
    hangup_cause = Column(String(100), nullable=True, index=True)  # Asterisk hangup cause
    error_message = Column(Text, nullable=True)  # Specific error messages
    
    # Metadata for call processing and tracking
    call_metadata = Column(JSON, nullable=True)  # Store additional call metadata as JSON

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    # Relationships
    campaign = relationship("AutodialCampaign", backref="calls_made")  # Changed backref to avoid conflict if 'Call' model is also used

    def __repr__(self):
        return f"<AutodialCall(id={self.id}, campaign_id={self.campaign_id}, phone='{self.phone_number}', status='{self.status}')>"
