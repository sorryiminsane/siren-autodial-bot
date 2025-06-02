# Standalone Autodial Bot for Telegram

This bot manages and executes auto-dialing campaigns. It interfaces with Telegram for user commands and an Asterisk server via AMI for call operations.

## Core Functionality

*   **Campaign Creation**: Users can upload a list of phone numbers (as a .txt file) to create a new dialing campaign.
*   **Call Origination**: The bot instructs Asterisk to dial numbers sequentially from the campaign list.
*   **Status Tracking**: Monitors the status of individual calls and overall campaign progress.
*   **AMI Integration**: Connects to Asterisk Manager Interface to send commands and receive call events.
*   **Database Persistence**: Stores campaign details, call lists, call statuses, and agent information using SQLAlchemy with an async PostgreSQL backend.
*   **Telegram Interface**: Uses `python-telegram-bot` for interactions.

## Setup Instructions

1.  **Prerequisites**:
    *   Python 3.8+
    *   PostgreSQL server
    *   Asterisk server configured with an AMI user and a dialplan context for handling autodialed calls.

2.  **Clone/Copy Files**:
    Ensure all bot files (`autodial_bot.py`, `autodial_database.py`, `autodial_models.py`, `autodial_requirements.txt`, `autodial.env.example`, `AUTODIAL_README.md`) are in your project directory (`C:\Users\mira\Projects\allblackcoupenohands\1111\singlep1\`).

3.  **Create a Virtual Environment** (recommended):
    ```bash
    python -m venv venv_autodial
    # Activate on Windows
    .\venv_autodial\Scripts\activate
    # Activate on Linux/macOS
    # source venv_autodial/bin/activate
    ```

4.  **Install Dependencies**:
    ```bash
    pip install -r autodial_requirements.txt
    ```

5.  **Configure Environment Variables**:
    *   Copy `autodial.env.example` to `autodial.env`.
    *   Edit `autodial.env` and fill in your specific details:
        *   `TELEGRAM_BOT_TOKEN`: Get this from BotFather on Telegram.
        *   `SUPER_ADMIN_ID`: Your numerical Telegram user ID for any special administrative functions.
        *   `DATABASE_URL`: Your PostgreSQL connection string (e.g., `postgresql+asyncpg://user:pass@host:port/dbname`).
        *   `AMI_HOST`, `AMI_PORT`, `AMI_USERNAME`, `AMI_SECRET`: Credentials for your Asterisk AMI.
        *   `DEFAULT_AUTODIAL_CALLER_ID`, `DEFAULT_AUTODIAL_TRUNK_CONTEXT`, `MAX_CONCURRENT_CALLS`: Default operational parameters.

6.  **Database Setup**:
    *   Ensure your PostgreSQL server is running and the specified database exists.
    *   The bot, on its first run (specifically `init_db()` in `autodial_database.py`), will attempt to create the necessary tables (`autodial_agents`, `autodial_campaigns`, `autodial_calls`) if they don't already exist.

7.  **Asterisk Dialplan Configuration**:
    You need a context in your Asterisk `extensions.conf` (e.g., `[from-autodial-bot]`) that the bot will use to originate calls. This context should:
    *   Answer the call.
    *   Play any desired prompts (e.g., "Press 1 to speak to an agent, press 2 to be removed from our list.").
    *   Collect DTMF input using `Read()` or `Background()`+`WaitExten()`.
    *   Send `UserEvent`s back to AMI with relevant information (Campaign ID, Call Record ID, DTMF pressed) so the bot can track responses.
    *   Hang up the call.
    *Example `extensions.conf` snippet:*
    ```ini
    [from-autodial-bot]
    exten => s,1,NoOp(Incoming autodial call for ${CHANNEL(CALL_RECORD_ID)} on campaign ${CHANNEL(CAMPAIGN_ID)})
     same => n,Answer()
     same => n,Wait(1) ; Wait a bit for audio path
     ; Example: Play a prompt and wait for DTMF
     same => n,Read(DTMF_DIGIT,your-prompt-filename,1,,1,5) ; Play prompt, 1 digit, 5s timeout
     same => n,GotoIf($["${DTMF_DIGIT}" = ""]?no_input)
     same => n,NoOp(User pressed: ${DTMF_DIGIT})
     same => n,UserEvent(AutoDialResponse,CallRecordID: ${CHANNEL(CALL_RECORD_ID)},CampaignID: ${CHANNEL(CAMPAIGN_ID)},DTMF: ${DTMF_DIGIT})
     same => n,Playback(your-thank-you-prompt) ; Optional
     same => n,Hangup()
    exten => s,n(no_input),NoOp(No DTMF input received)
     same => n,UserEvent(AutoDialResponse,CallRecordID: ${CHANNEL(CALL_RECORD_ID)},CampaignID: ${CHANNEL(CAMPAIGN_ID)},DTMF: TIMEOUT)
     same => n,Hangup()

    exten => h,1,NoOp(Call hung up for ${CHANNEL(CALL_RECORD_ID)})
    ; You might send another UserEvent here on hangup if needed
    ```

8.  **Run the Bot**:
    ```bash
    python autodial_bot.py
    ```

## Bot Commands (Initial)

*   `/start`: Initializes the bot for the user if authorized.
*   `/newcampaign`: Starts the process to create a new autodial campaign by prompting for a file upload.
*   (Inside campaign setup) `/startcampaign`: Confirms and starts the uploaded campaign.
*   (Inside campaign setup) `/cancelcampaign`: Cancels the current file upload/campaign setup process.

## Project Structure

*   `autodial_bot.py`: Main application logic, Telegram handlers, AMI interactions, campaign processing.
*   `autodial_database.py`: SQLAlchemy setup, async database session management, `init_db` function.
*   `autodial_models.py`: SQLAlchemy ORM models (`Agent`, `AutodialCampaign`, `AutodialCall`).
*   `autodial_requirements.txt`: Python package dependencies.
*   `autodial.env`: Local environment configuration (created from `autodial.env.example`).
*   `autodial.env.example`: Template for environment variables.
*   `AUTODIAL_README.md`: This documentation file.

## Further Development (TODO)

*   Implement campaign pausing, resuming, and stopping for active campaigns.
*   Add more detailed status reporting commands (e.g., `/campaignstatus <id>`).
*   Refine AMI event handling for more call states (Busy, No Answer, etc.).
*   Implement retry logic for failed calls.
*   Add agent-specific settings management via bot commands.
*   Improve error handling and notifications to admins/agents.
*   Develop a more sophisticated concurrent call management within `process_campaign`.
