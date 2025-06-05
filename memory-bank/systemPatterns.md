# System Patterns

This file documents recurring patterns and standards used in the project.
It is optional, but recommended to be updated as the project evolves.
"2024-12-19 15:30:00" - Initial system patterns documentation.

## Coding Patterns

### Async Database Operations
```python
async with get_db_session() as session:
    result = await session.execute(select(Agent).filter_by(telegram_id=user_id))
    agent = result.scalar_one_or_none()
    # Database operations
    await session.commit()
```
**Usage**: Consistent pattern for all database interactions using async context managers

### Error Handling in Handlers
```python
try:
    # Handler logic
    await some_operation()
except SQLAlchemyError as e:
    logger.error(f"Database error: {str(e)}")
    await update.message.reply_text("Database error. Please try again.")
except Exception as e:
    logger.error(f"Error in handler: {str(e)}")
    await update.message.reply_text("An error occurred.")
```
**Usage**: Standardized error handling with logging and user feedback

### AMI Event Processing
```python
async def event_listener(manager, event):
    event_dict = dict(event)
    logger.debug(f"Event: {event_dict}")
    
    # Extract event data
    uniqueid = event.get('Uniqueid')
    channel = event.get('Channel')
    
    # Process in database context
    async with get_async_db_session() as session:
        # Event processing logic
```
**Usage**: Standard pattern for AMI event handling with debugging and database integration

## Architectural Patterns

### Two-Stage Call Origination
1. **Agent Channel**: Call agent's phone first
2. **Target Channel**: When agent answers, dial target number
3. **Bridge**: Connect both channels when target answers

**Benefits**: Agent always in control, no missed calls, proper caller ID handling

### Pre-Created Call Records
1. **Upload Processing**: Validate and parse phone numbers
2. **Batch Creation**: Create all Call records in database first
3. **Async Origination**: Process pre-created records with concurrency limits
4. **Status Tracking**: Update records throughout call lifecycle

**Benefits**: Better error handling, progress tracking, database consistency

### Event-Driven Status Updates
- AMI events trigger database updates
- Database changes trigger Telegram notifications
- Status messages updated in real-time via callback queries

**Benefits**: Real-time user feedback, audit trail, system observability

## Testing Patterns

### Database Session Management
- Use async context managers for all database operations
- Separate sessions for different operations to avoid conflicts
- Explicit commit/rollback handling for error scenarios

### AMI Event Simulation
- Event listener functions accept event dictionaries
- Mock AMI events for testing event processing logic
- Database state verification after event processing

### Telegram Bot Testing
- Conversation handler state verification
- Callback query response testing
- Message editing and reply testing 