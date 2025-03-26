# TGSip Project Context
WE ARE MAKING STRICTLY OUTBOUND CALLS, WE DIAL ONLY VIA OUTBOUND CALL. NO SIP ENDPOINTS. AGENTS & TARGETS ARE DIALED VIA OUTBOUND CALLS

## Project Overview
TGSip is a Telegram bot that enables agents to initiate outbound calls through Asterisk without requiring SIP clients. The system bridges calls between agents and their targets using PSTN, with all interaction happening through Telegram.

## Core Components
1. **Telegram Bot Interface**
   - Handles agent interactions
   - Provides command interface
   - Manages authorization

2. **Asterisk Server (18.9 Certified)**
   - Manages telephony operations
   - Handles outbound PSTN calls
   - Uses IP-authenticated SIP trunk

3. **Database Layer**
   - PostgreSQL database
   - Stores agent information
   - Manages authorizations

4. **Route Management System**
   - Main Route
   - Development Route
   - Per-agent route persistence

## Technical Standards
1. **Authentication**
   - SIP trunks: IP-based authentication
   - Agents: Telegram-based authorization
   - Super admin controls via Telegram ID

2. **Number Formatting**
   - E.164 format support
   - US format support
   - Validation on input

3. **Call Flow Protocol**
   1. Agent initiates via `/call`
   2. Request validation
   3. Database lookup
   4. AMI trigger
   5. Call bridging
   6. Status updates

## Project Organization
- `bot.py`: Main bot logic
- `database.py`: Database operations
- `models.py`: Data models
- Configuration files:
  - `pjsip.conf`
  - `extensions.conf`
  - `loggy.yaml`
  - `.env`
