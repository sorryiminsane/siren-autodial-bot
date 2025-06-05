# Product Context

This file provides a high-level overview of the project and the expected product that will be created. Initially it is based upon projectBrief.md (if provided) and all other available project-related information in the working directory. This file is intended to be updated as the project evolves, and should be used to inform all other modes of the project's goals and context.

"2024-12-19 15:30:00" - Initial creation based on comprehensive technical analysis of existing SIREN codebase.

## Project Goal

SIREN is a sophisticated Telegram bot-based call center management system that enables agents to make outbound calls through Asterisk PBX without requiring traditional SIP clients. The system integrates Telegram's messaging platform with Asterisk's telephony capabilities via the Asterisk Manager Interface (AMI).

## Key Features

### Core Telephony Features
- **Manual Outbound Calling**: Agents can initiate calls via `/call` command through Telegram
- **Auto-Dial Campaigns**: Upload .txt files with phone numbers for automated dialing campaigns
- **Two-Stage Dialing**: Agent answers first, then target is dialed for seamless call flow
- **Interactive Call Menu (ICM)**: Real-time call control buttons (mute, hold, transfer, hangup)
- **DTMF Detection**: Real-time notifications when call recipients press phone keys
- **Multi-Route Support**: Main, Red, and Black routes with different trunk configurations

### Agent Management
- **Authorization System**: Super admin can authorize/deauthorize agents
- **Phone Number Registration**: Agents must register their phone numbers for callbacks
- **Caller ID Configuration**: Separate caller IDs for manual calls and auto-dial campaigns
- **Route Selection**: Agents can choose between different call routes

### Database Integration
- **PostgreSQL Backend**: Full call tracking and agent management
- **SQLAlchemy 2.0**: Async ORM for database operations
- **Call History**: Complete call records with metadata, status tracking, and DTMF history
- **Campaign Management**: Auto-dial campaign creation and response tracking

### Real-Time Integration
- **Asterisk AMI**: Live connection to Asterisk for call control and event monitoring
- **Event Processing**: Real-time handling of call events (hangup, bridge, DTMF, etc.)
- **Status Updates**: Live call status updates in Telegram messages

## Overall Architecture

### Technology Stack
- **Backend**: Python 3.x with async/await patterns
- **Bot Framework**: python-telegram-bot v20.8
- **Database**: PostgreSQL with SQLAlchemy 2.0.27 (async)
- **Telephony**: Asterisk PBX with AMI integration via Panoramisk
- **Event Handling**: Async AMI event listeners for real-time call monitoring

### Key Components
1. **Bot Interface** (`bot.py`): Main Telegram bot application with conversation handlers
2. **Database Models** (`models.py`): Agent, Call, AutodialCampaign, AutodialResponse entities
3. **Database Layer** (`database.py`): Async connection management
4. **Asterisk Config**: PBX configuration files for trunks, contexts, and IVR

### Call Flow Architecture
1. **Manual Calls**: Agent → Telegram command → AMI originate → Two-stage dial
2. **Auto-Dial**: File upload → Campaign creation → Batch origination → IVR context
3. **Event Processing**: Asterisk events → AMI listener → Database updates → Telegram notifications

### Security & Access Control
- Super admin authorization system
- Agent phone number verification
- Trunk-based route isolation
- Call tracking with full audit trail 