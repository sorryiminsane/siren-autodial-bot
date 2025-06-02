## Auto-Dial Feature In-Depth Summary

The auto-dial system allows agents to upload a list of phone numbers that will be automatically dialed in sequence. This document provides a detailed overview of its functionality, components, and potential areas for improvement, which can be useful if considering separating this feature into a standalone bot.

### 1. Core Components & Architecture

The auto-dial feature is integrated within the main Telegram bot application and relies on several key architectural pieces:

*   **Telegram Bot Interface**: Handles user interactions, command processing (`/autodial`), file uploads, and feedback messages.
*   **Asterisk Integration**: Leverages an Asterisk telephony server for actual call origination and management. Communication is typically via:
    *   **AMI (Asterisk Manager Interface)**: Used to send commands (e.g., originate call) and receive events (e.g., call status, DTMF responses).
*   **Database**: Persists campaign data, call lists, call statuses, and agent configurations. SQLAlchemy is used as the ORM.
*   **Conversation Handler**: Manages the stateful interaction for the auto-dial setup process within the Telegram bot.

Key Python modules and functions involved:
*   `bot.py`: Contains the primary logic.
    *   `handle_autodial_command()`: Entry point when an agent initiates an auto-dial campaign.
    *   `handle_auto_dial_file()`: Processes the uploaded text file of numbers, validates them, and prepares the campaign.
    *   `originate_autodial_call()` / `originate_autodial_call_from_record()`: Functions responsible for instructing Asterisk to place calls.
    *   AMI event listeners (within `post_init`): To capture call progress, DTMF responses, and hangup events from Asterisk.
*   `models.py`:
    *   `AutodialCampaign`: Stores information about each campaign (e.g., name, agent ID, creation time).
    *   `AutodialResponse`: Records responses received during calls (e.g., if a contact pressed '1').
    *   `Call`: A general model likely used to track individual call legs, including those for auto-dialing, storing details like `call_id`, `tracking_id`, `status`, `uniqueid` (from Asterisk), etc.
    *   `Agent`: Stores agent-specific settings relevant to auto-dialing, such as `autodial_trunk`, `autodial_caller_id`, and the `auto_dial` feature flag.
*   `extensions.conf` (Asterisk dialplan): Defines how Asterisk handles the outbound auto-dial calls, including playing prompts, collecting DTMF, and firing UserEvents back to the bot via AMI.
*   `pjsip.conf` (Asterisk PJSIP configuration): Configures SIP trunks used for placing the auto-dial calls.

### 2. User Workflow & Functionality

1.  **Initiation**:
    *   An authorized agent triggers the auto-dial feature via the `/autodial` command or a button in the bot's main menu.
    *   The bot enters the `AUTO_DIAL` conversation state.
2.  **Campaign Setup**:
    *   The bot prompts the agent to upload a `.txt` file.
    *   The file should contain one phone number per line, preferably in E.164 format.
    *   The agent might be asked to provide a name for the campaign (or one is auto-generated).
3.  **File Processing & Validation**:
    *   `handle_auto_dial_file()` reads the uploaded file.
    *   Each number is validated (e.g., using regex for E.164 format).
    *   Invalid numbers are typically skipped or reported to the agent.
4.  **Database Campaign Creation**:
    *   A new `AutodialCampaign` record is created in the database.
    *   Individual `Call` records are pre-created for each valid phone number in the list, associated with the campaign. These records might initially have a status like "pending" or "queued".
5.  **Call Origination & Management**:
    *   The system iterates through the list of numbers for the campaign.
    *   `originate_autodial_call()` or a similar function constructs an AMI `Originate` action.
        *   **Context**: Specifies the Asterisk dialplan context to route the call (e.g., `from-autodial-one` or `from-autodial-two`).
        *   **Extension**: The target number to dial.
        *   **CallerID**: Uses the agent's configured `autodial_caller_id`.
        *   **Variables**: Passes crucial information like `CampaignID`, `AgentTelegramID`, `TrackingID` (a unique ID for this specific call attempt within the campaign) to Asterisk. These are vital for linking Asterisk events back to the correct campaign and call record.
    *   The bot may manage call concurrency (e.g., limiting the number of simultaneous outbound calls for a campaign or per agent).
    *   The `active_campaigns` global dictionary likely tracks running campaigns and their progress.
6.  **Asterisk Call Handling (Dialplan)**:
    *   Asterisk receives the `Originate` command.
    *   The call is processed by the specified context in `extensions.conf`.
    *   Typically, the dialplan will:
        *   Play a pre-recorded audio prompt (e.g., "Press 1 to connect...").
        *   Use the `Read()` application to collect DTMF input from the called party.
        *   Based on the DTMF input (or lack thereof), perform actions like:
            *   Playing another message.
            *   Hanging up.
            *   Sending a `UserEvent` via AMI back to the bot with the outcome.
7.  **Event Handling & Feedback**:
    *   The bot's AMI listener (in `post_init`) receives events from Asterisk:
        *   `UserEvent` (custom events defined in `extensions.conf`): These are crucial for getting structured data about call progress, such as `AutoDialResponse` (containing `AgentID`, `CallerID`, `PressedOne`, `CampaignID`, `TrackingID`).
        *   Standard AMI events like `Hangup`, `DialState`.
    *   The bot processes these events:
        *   Updates the status of the corresponding `Call` record in the database (e.g., "answered", "no_answer", "completed", "error").
        *   Records any DTMF responses in the `AutodialResponse` table.
        *   Provides feedback to the agent via Telegram (e.g., campaign progress, number of successful calls, errors).
8.  **Campaign Completion**: Once all numbers in the list have been attempted, the campaign is marked as complete. The agent might receive a summary report.

### 3. Key Features & Design Considerations

*   **Authorization**: Only agents with the `auto_dial` flag enabled and potentially other permissions can use this feature.
*   **Trunk Selection**: Agents can often select a specific SIP trunk (`autodial_trunk` in `Agent` model) for their campaigns, allowing for different routing or cost profiles.
*   **Caller ID Management**: Separate Caller ID for auto-dial campaigns (`autodial_caller_id`) distinct from manual call Caller ID.
*   **Concurrency Control**: The system might have mechanisms to limit how many calls are dialed simultaneously, either globally or per agent, to avoid overwhelming the Asterisk server or SIP trunks.
*   **Error Handling**: Includes handling for invalid numbers, AMI connection issues, call origination failures, and database errors.
*   **Logging**: Extensive logging is crucial for diagnostics and auditing.
*   **Database Tracking**: Detailed records of campaigns, individual call attempts, and their outcomes are stored for reporting and analysis.
*   **Scalability**: The current implementation's scalability will depend on the efficiency of AMI interactions, database queries, and Python's asynchronous handling.

### 4. Potential Areas for Improvement / Considerations for Standalone Bot

*   **Retry Logic**: Implement configurable retry attempts for numbers that fail (e.g., busy, no answer).
*   **DNC List Integration**: Check numbers against a Do Not Call list before dialing.
*   **Advanced Scheduling**: Allow campaigns to be scheduled for specific times/dates.
*   **Pacing/Throttling**: More sophisticated call pacing algorithms (e.g., predictive dialing, adjusting dialing rate based on agent availability if calls are to be transferred).
*   **Reporting & Analytics**: Enhanced in-bot or external reporting on campaign performance.
*   **Real-time Dashboard**: A web interface for monitoring active campaigns.
*   **API for External Control**: If it becomes a separate service, an API would be needed for other systems to trigger and manage campaigns.
*   **Resource Management**: Careful management of Asterisk channel resources and database connections.
*   **Configuration Management**: Easier way to manage auto-dial specific settings (e.g., concurrency limits, default prompts) if separated.
*   **Dedicated Event Processing**: If standalone, it would need its own robust AMI event processing loop, potentially more resilient than one shared within a larger bot.
*   **Authentication/Authorization for API**: If it offers an API, secure methods for clients to authenticate.

## 5. Technical Deep Dive

### Call Origination Process

#### `originate_autodial_call` Function
- **Input Parameters**:
  - `context`: Application context
  - `target_number`: E.164 formatted phone number
  - `trunk`: SIP trunk identifier (e.g., 'autodial-one')
  - `caller_id`: Caller ID to display
  - `agent_telegram_id`: ID of initiating agent
  - `campaign_id`: Optional campaign ID
  - `sequence_number`: Position in campaign

#### Key Operations:
1. **Call Identification**
   - Generates unique `call_id` with microsecond precision
   - Creates human-readable `tracking_id` (e.g., JKD1.1)
   - Tracks action ID for AMI correlation

2. **Database Record Creation**
   ```python
   new_call = Call(
       call_id=call_id,
       campaign_id=campaign_id,
       sequence_number=sequence_number,
       tracking_id=tracking_id,
       agent_telegram_id=agent_telegram_id,
       target_number=target_number,
       caller_id=caller_id,
       trunk=trunk,
       channel=f'PJSIP/{target_number}@{trunk}',
       action_id=action_id,
       status="initiated",
       start_time=datetime.now(),
       call_metadata={
           "timestamp": int(time.time()),
           "origin": "autodial",
           "tracking_id": tracking_id
       }
   )
   ```

3. **AMI Originate Action**
   - Sets up persistent variables with double underscores
   - Configures channel and context
   - Implements 45-second timeout
   - Enables async operation

4. **Error Handling**
   - Catches AMI and database errors
   - Updates call status appropriately
   - Maintains detailed error logs

### Event Processing System

#### Hangup Event Handler
1. **Event Capture**
   - Listens for AMI Hangup events
   - Extracts call identifiers (Uniqueid, Channel, TrackingID)
   - Captures hangup cause and timing

2. **Call Lookup**
   - Tries multiple methods to find call record:
     1. Uniqueid/CallID match
     2. CallID from event variables
     3. TrackingID
     4. Channel name

3. **Status Update**
   - Sets call status to 'completed'
   - Records end time
   - Updates call metadata with hangup details
   - Commits changes to database

4. **Agent Notification**
   - Sends completion message via Telegram
   - Includes call duration and status
   - Handles notification errors gracefully

### Database Schema Details

#### Calls Table Structure
```sql
CREATE TABLE calls (
    id SERIAL PRIMARY KEY,
    call_id VARCHAR(255) UNIQUE NOT NULL,
    campaign_id INTEGER REFERENCES autodial_campaigns(id),
    sequence_number INTEGER,
    tracking_id VARCHAR(50),
    agent_telegram_id BIGINT NOT NULL,
    target_number VARCHAR(50) NOT NULL,
    caller_id VARCHAR(50),
    trunk VARCHAR(50) NOT NULL,
    channel VARCHAR(255),
    action_id VARCHAR(255),
    status VARCHAR(50) NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    call_metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Performance Considerations

1. **Database Optimization**
   - Indexes on frequently queried fields
   - Connection pooling for high concurrency
   - Batch operations where possible

2. **AMI Connection Management**
   - Persistent connection with reconnection logic
   - Event buffering during disconnections
   - Rate limiting to prevent overload

3. **Resource Management**
   - Limits on concurrent calls
   - File size restrictions
   - Memory usage monitoring

### Security Implementation

1. **Input Validation**
   - Strict E.164 number validation
   - File content sanitization
   - Size and type restrictions

2. **Access Control**
   - Agent authorization checks
   - Feature flag enforcement
   - Session validation

3. **Data Protection**
   - Secure credential handling
   - Sensitive data masking in logs
   - Audit trail maintenance

## 6. Migration to Standalone Service

### Required Components
1. **Core Service**
   - AMI connection manager
   - Database access layer
   - Task scheduler
   - API server

2. **Dependencies**
   - Python 3.8+
   - SQLAlchemy 2.0+
   - Panaroma-AMI
   - FastAPI (for API)
   - Redis (for task queue)

3. **Configuration**
   - Environment variables
   - YAML config files
   - Database migrations

### Implementation Steps
1. **Phase 1: Core Extraction**
   - Move auto-dial logic to new service
   - Implement basic API endpoints
   - Set up database access

2. **Phase 2: Feature Parity**
   - Replicate all current functionality
   - Ensure AMI event handling
   - Implement status reporting

3. **Phase 3: Enhancement**
   - Add new features
   - Improve error handling
   - Implement monitoring

This technical deep dive provides the foundation for understanding and potentially migrating the auto-dial functionality to a standalone service.

## 7. Asterisk AMI Interaction Details

### 7.1 Connection Setup
* `post_init()` (in `bot.py`) is executed after the Telegram `Application` is built.
* It instantiates a `panoramisk.Manager` object:
  ```python
  ami_manager = Manager(
      host=AMI_HOST,
      port=AMI_PORT,
      username=AMI_USERNAME,
      secret=AMI_SECRET,
      encoding='utf8'
  )
  await ami_manager.connect()
  ```
* The manager re-uses PTB’s asyncio loop. It will automatically reconnect if the TCP socket drops (internal to panoramisk).

### 7.2 Event Listener Registration
Inside `post_init()` two coro‐callbacks are registered:
1. **`ami_event_listener()`** – subscribed to *all* AMI events.
2. **`new_channel_event_listener()`** – subscribed specifically to `Newchannel`.

`ami_manager.register_event` is not invoked explicitly; panoramisk lets you `await ami_manager.connect()` and then call `ami_manager.add_event_handler(cb)` implicitly (already done in earlier code – omitted here for brevity). Both handlers receive two args `(manager, event)` where `event` is a dict-like `Message`.

### 7.3 Event Processing Logic
| AMI Event | Handler Path | Purpose |
|-----------|--------------|---------|
| `Hangup`  | `ami_event_listener` lines 1465-1469 | Mark call `completed` in in-memory `active_calls` and DB. |
| `DTMFEnd` | `ami_event_listener` lines 1471-1476 | Flag that DTMF was received (generic). |
| `UserEvent` **AutoDialResponse** | `ami_event_listener` lines 1481-1535 | Detects *PressedOne* “Yes”. Records `AutodialResponse`, notifies agent. |
| `UserEvent` **KeyPress**         | Alternative dial-plan event lines 1539-1589 | Generic key-press with headers `Number`, `Pressed`, `Campaign`. |
| `Newchannel` | `new_channel_event_listener` lines 1593-1620 | Maps Asterisk `Uniqueid ↔ call_id` for later look-ups. |

### 7.4 DTMF / "1" Detection Flow
1. **Dial-plan** (`extensions.conf`) fires:
   ```asterisk
   same => n,UserEvent(AutoDialResponse,AgentID:${AgentTelegramID},CallerID:${CALLERID(num)},PressedOne:${PressedOne},CampaignID:${CampaignID})
   ```
   `PressedOne` is set to `Yes` in the dial-plan if `${DTMF_response} == 1`.
2. **Bot** receives `event.get('UserEvent') == 'AutoDialResponse'`.
3. Extracts:
   * `AgentID` → Telegram chat to notify.
   * `CallerID` → E.164 of callee.
   * `PressedOne` → string `Yes/No`.
   * `CampaignID` → FK to `autodial_campaigns` (can be `'unknown'`).
4. If **`PressedOne == 'Yes'`**:
   * Insert `AutodialResponse(campaign_id, phone_number, '1')`.
   * Compose markdown message:
     ```
     ✅ *New Auto-Dial Response*
     📱 Phone: +15551234567
     🔘 Response: Pressed 1
     Campaign: My Campaign
     ```
   * `application.bot.send_message(chat_id=agent_id_int, …)`.
5. Else → log “did not press 1”.

### 7.5 State Association Strategies
* **Primary key**: `CallID` (passed via channel variable and AMI `ChannelId`).
* **Fallbacks**: `Uniqueid`, `Channel`, `TrackingID`.
* `new_channel_event_listener` captures `Newchannel` to back-fill `Uniqueid → call_id` mapping when Asterisk creates the channel before dial-plan variables propagate.

### 7.6 Error & Edge-Case Handling
* Gracefully handles missing `AgentID` or non-numeric IDs (`ValueError`).
* If `campaign_id == 'unknown'`, the response is still logged but not tied to a specific campaign.
* Database exceptions are wrapped in `async with get_session()` ensuring rollback.
* Notifications are surrounded by try/except to avoid crashing the listener.

### 7.7 Dependencies & Assumptions
* **panoramisk** ≥ 1.4 for asyncio AMI client.
* Dial-plan must set `AgentTelegramID`, `CampaignID`, and `PressedOne` channel vars.
* Telegram bot token & AMI creds loaded from `.env`.
* PostgreSQL schema must include `autodial_campaigns`, `autodial_responses`, `calls` tables as defined earlier.
* Network latency between bot and Asterisk is low enough (<1-2 s) for timely UserEvents.

---
This section fully documents how the bot connects to AMI, listens for events, associates DTMF “1” presses to campaigns/calls, and notifies the responsible agent.