# System Patterns

This file documents recurring patterns and standards used in the project.
"2024-03-25 20:17:00" - Initial file creation

## Coding Patterns

### Command Pattern (Telegram Bot)
- Commands follow `/command` format
- Each command has dedicated handler function
- Consistent error handling and response formatting

### Configuration Management
- Environment variables in `.env`
- Asterisk configs in separate files (`pjsip.conf`, `extensions.conf`)
- Logging configuration in `loggy.yaml`

### Database Operations
- PostgreSQL with direct connection via DATABASE_URL
- Models defined in `models.py`
- Database operations centralized in `database.py`

## Architectural Patterns

### Two-Stage Call Flow Pattern
1. First stage: Agent call initiation
2. Second stage: Target call connection
3. Final stage: Call bridging

### Authentication Patterns
1. SIP Trunks: IP-based authentication
2. Agents: Telegram ID-based authorization
3. Super admin: Special Telegram ID privileges

### Route Management Pattern
- Dual-route system (Main/Development)
- Per-agent route persistence
- Route selection based on agent configuration

## Testing Patterns

### Environment Separation
- Development route for testing
- Production route for live calls
- Separate configurations per environment

### Number Validation Patterns
- E.164 format validation
- US format support
- Pre-call validation checks

### Call Flow Testing
- Agent connection verification
- Target number validation
- Bridge success confirmation 