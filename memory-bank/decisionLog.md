# Decision Log
WE ARE MAKING STRICTLY OUTBOUND CALLS, WE DIAL ONLY VIA OUTBOUND CALL. NO SIP ENDPOINTS. AGENTS & TARGETS ARE DIALED VIA OUTBOUND CALLS

## Architecture Decisions

### Authentication System
- **Decision**: Use IP-based authentication for SIP trunks
- **Rationale**: Simpler setup, no credential management needed
- **Implementation**: Whitelist Asterisk server IP in SIP provider

### Database Choice
- **Decision**: PostgreSQL
- **Rationale**: Robust, scalable, supports complex queries
- **Implementation**: Direct connection via DATABASE_URL

### Route Management
- **Decision**: Two-route system (Main/Development)
- **Rationale**: Separate production and testing environments
- **Implementation**: Per-agent route persistence in database

### Call Flow
- **Decision**: Two-stage call process
- **Rationale**: Better control and monitoring of call setup
- **Implementation**: 
  1. Call agent first
  2. Call target after agent pickup
  3. Bridge calls

## Technical Decisions

### Number Format
- **Decision**: Support E.164 and US formats
- **Rationale**: Flexibility for users while maintaining standardization
- **Implementation**: Validation at input level

### Bot Interface
- **Decision**: Command-based with menu support
- **Rationale**: Simple to use, familiar to Telegram users
- **Implementation**: Combined command and button interface
