import asyncio
import logging
import os
import re
import time
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from database import init_db, get_session as get_async_db_session
from models import Agent, AutodialCampaign, AutodialResponse, Call
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from contextlib import asynccontextmanager
from panoramisk import Manager
import asyncio
import json
from typing import Optional, Dict, Any

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))

# AMI Configuration
AMI_HOST = '127.0.0.1'
AMI_PORT = 5038
AMI_USERNAME = 'tgsipbot'
AMI_SECRET = 'lovemeless'

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Admin/Logging Configuration
LOG_GROUP_ID = -4865417097  # Log group for failure notifications
MAX_RETRY_ATTEMPTS = 3      # Maximum retry attempts per call
RETRY_DELAY_SECONDS = 90    # Delay between retry attempts

# Conversation states
(MAIN_MENU, SETTINGS, PHONE_SETTINGS, 
 CALL_MENU, AGENT_MANAGEMENT, AUTO_DIAL, AGENT_ID_INPUT) = range(7)

# Global in-memory data structures
active_calls = {}
pending_originations = {}
uniqueid_to_call_id = {}
channel_to_call_id = {}
active_campaigns = {}

# P1 Production Campaign Management
campaign_states = {}  # campaign_id: CampaignState
campaign_messages = {}  # campaign_id: {"chat_id": int, "message_id": int}
notification_queue = []  # Rate-limited notification queue

class CampaignState:
    """Track real-time campaign statistics and settings."""
    def __init__(self, campaign_id: int, user_id: int, total_calls: int):
        self.campaign_id = campaign_id
        self.user_id = user_id
        self.total_calls = total_calls
        self.completed_calls = 0
        self.active_calls = 0
        # HIDDEN from users - tracked internally for admin/retry logic
        self.failed_calls = 0
        self.blocked_calls = 0
        self.dtmf_responses = 0
        self.is_paused = False
        self.individual_notifications = False  # Toggleable setting
        self.start_time = datetime.now()
        self.last_update = datetime.now()
        # Retry system
        self.retry_queue = []  # List of calls to retry: [{"call_id": str, "target_number": str, "retry_count": int, "last_attempt": datetime}]
        self.total_retries_processed = 0
        
    def get_progress_bar(self, width=10):
        """Generate progress bar for campaign."""
        if self.total_calls == 0:
            return "▱" * width
        progress = (self.completed_calls + self.failed_calls) / self.total_calls
        filled = int(progress * width)
        return "▰" * filled + "▱" * (width - filled)
        
    def get_completion_percentage(self):
        """Get completion percentage (user-facing - only completed calls)."""
        if self.total_calls == 0:
            return 0
        # Only count completed calls for user-facing percentage
        return int((self.completed_calls / self.total_calls) * 100)
    
    def get_actual_completion_percentage(self):
        """Get actual completion percentage including failed/blocked (admin-only)."""
        if self.total_calls == 0:
            return 0
        return int(((self.completed_calls + self.failed_calls + self.blocked_calls) / self.total_calls) * 100)

global_application_instance = None # Declare global variable for application instance

async def update_campaign_message(campaign_id: int):
    """Update the campaign status message in real-time."""
    if campaign_id not in campaign_states or campaign_id not in campaign_messages:
        return
        
    campaign = campaign_states[campaign_id]
    message_info = campaign_messages[campaign_id]
    
    # Calculate duration
    duration = datetime.now() - campaign.start_time
    duration_str = f"{int(duration.total_seconds() // 60)}m {int(duration.total_seconds() % 60)}s"
    
    # Build status message (user-facing - clean metrics only)
    processed_calls = campaign.completed_calls + campaign.failed_calls + campaign.blocked_calls
    # Don't let processed exceed total (in case of counting bugs)
    if processed_calls > campaign.total_calls:
        processed_calls = campaign.total_calls
    
    status_text = (
        f"🤖 **P1 Campaign #{campaign_id}**\n\n"
        f"📊 **Progress** {campaign.get_completion_percentage()}%\n"
        f"{campaign.get_progress_bar()} ({processed_calls}/{campaign.total_calls})\n\n"
        f"📞 **Call Stats**\n"
        f"├─ ✅ Completed: {campaign.completed_calls}\n"
        f"├─ ❌ Failed: {campaign.failed_calls}\n"
        f"├─ 🔄 Active: {campaign.active_calls}\n"
        f"└─ 🔔 DTMF Responses: {campaign.dtmf_responses}\n\n"
        f"⏱ **Duration:** {duration_str}\n"
        f"⚡ **Status:** {'⏸ Paused' if campaign.is_paused else '🚀 Running'}"
    )
    
    # Campaign control buttons
    keyboard = []
    if campaign.is_paused:
        keyboard.append([InlineKeyboardButton("▶️ Resume", callback_data=f"resume_campaign_{campaign_id}")])
    else:
        keyboard.append([InlineKeyboardButton("⏸ Pause", callback_data=f"pause_campaign_{campaign_id}")])
    
    keyboard.extend([
        [
            InlineKeyboardButton("📊 Details", callback_data=f"campaign_details_{campaign_id}"),
            InlineKeyboardButton("🔔 Notifications", callback_data=f"campaign_notifications_{campaign_id}")
        ],
        [
            InlineKeyboardButton("🛑 Stop", callback_data=f"stop_campaign_{campaign_id}"),
            InlineKeyboardButton("🔙 Menu", callback_data="back_main")
        ]
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await global_application_instance.bot.edit_message_text(
            chat_id=message_info["chat_id"],
            message_id=message_info["message_id"],
            text=status_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        campaign.last_update = datetime.now()
    except Exception as e:
        logger.error(f"Failed to update campaign message {campaign_id}: {e}")

async def queue_call_for_retry(campaign_id: int, call, retry_count: int, failure_type: str, failure_reason: str):
    """Queue a failed call for retry with delay."""
    if campaign_id not in campaign_states:
        return
        
    campaign = campaign_states[campaign_id]
    
    # Add to retry queue
    retry_entry = {
        "call_id": call.call_id,
        "target_number": call.target_number,
        "retry_count": retry_count,
        "last_attempt": datetime.now(),
        "failure_type": failure_type,
        "failure_reason": failure_reason,
        "call_metadata": call.call_metadata or {}
    }
    
    campaign.retry_queue.append(retry_entry)
    
    # Send admin notification about retry being queued
    await send_admin_failure_log(campaign_id, "retry_queued", {
        "target_number": call.target_number,
        "retry_count": retry_count,
        "failure_reason": failure_reason
    })
    
    logger.info(f"Campaign {campaign_id}: Queued {call.target_number} for retry {retry_count}/{MAX_RETRY_ATTEMPTS} (reason: {failure_reason})")

async def process_retry_queue():
    """Process retry queues for all active campaigns."""
    for campaign_id, campaign in campaign_states.items():
        if campaign.is_paused or not campaign.retry_queue:
            continue
            
        # Check for calls ready to retry (90 seconds have passed)
        ready_to_retry = []
        current_time = datetime.now()
        
        for retry_entry in campaign.retry_queue[:]:  # Copy list to avoid modification during iteration
            time_since_last = (current_time - retry_entry["last_attempt"]).total_seconds()
            
            if time_since_last >= RETRY_DELAY_SECONDS:
                ready_to_retry.append(retry_entry)
                campaign.retry_queue.remove(retry_entry)
        
        # Process ready retries
        for retry_entry in ready_to_retry:
            await execute_retry_call(campaign_id, retry_entry)

async def retry_queue_processor():
    """Background task to continuously process retry queues."""
    logger.info("Starting retry queue processor")
    
    while True:
        try:
            await process_retry_queue()
            # Check every 30 seconds for retry opportunities
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Error in retry queue processor: {e}")
            # Continue running even if there's an error
            await asyncio.sleep(30)

async def execute_retry_call(campaign_id: int, retry_entry: dict):
    """Execute a retry call attempt."""
    try:
        # Get campaign state
        campaign = campaign_states[campaign_id]
        target_number = retry_entry["target_number"]
        retry_count = retry_entry["retry_count"]
        
        # Get agent info for the call
        async with get_async_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=campaign.user_id))
            agent = result.scalar_one_or_none()
            
            if not agent:
                logger.error(f"Agent not found for retry call in campaign {campaign_id}")
                return
                
            caller_id = agent.autodial_caller_id if agent.autodial_caller_id else None
            trunk = f"autodial-{agent.route}" if agent.route else "autodial-one"
            
            # Create new call record for retry
            timestamp = int(time.time())
            import uuid
            unique_suffix = str(uuid.uuid4())[:8]
            new_call_id = f"retry_{campaign_id}_{timestamp}_{retry_count}_{unique_suffix}"
            tracking_id = f"JKD1.R{retry_count}.{timestamp % 1000}"
            
            # Copy original call metadata and add retry info
            call_metadata = retry_entry.get("call_metadata", {})
            call_metadata.update({
                "retry_count": retry_count,
                "original_call_id": retry_entry["call_id"],
                "retry_reason": retry_entry["failure_reason"],
                "retry_timestamp": datetime.now().isoformat()
            })
            
            new_call = Call(
                call_id=new_call_id,
                campaign_id=campaign_id,
                tracking_id=tracking_id,
                agent_telegram_id=campaign.user_id,
                target_number=target_number,
                caller_id=caller_id,
                trunk=trunk,
                status="queued",
                start_time=datetime.now(),
                call_metadata=call_metadata
            )
            session.add(new_call)
            await session.commit()
            
            logger.info(f"Created retry call record {new_call_id} for {target_number} (attempt {retry_count})")
        
        # Originate the retry call
        result = await originate_autodial_call_from_record(
            call_id=new_call_id,
            tracking_id=tracking_id
        )
        
        if result.get('success', False):
            # Update campaign state - mark as active (don't increment total_calls for retries)
            campaign.active_calls += 1
            campaign.total_retries_processed += 1
            
            # Update campaign message
            await update_campaign_message(campaign_id)
            
            logger.info(f"Campaign {campaign_id}: Successfully initiated retry call to {target_number} (attempt {retry_count})")
        else:
            logger.error(f"Campaign {campaign_id}: Failed to initiate retry call to {target_number}: {result.get('message', 'Unknown error')}")
            
    except Exception as e:
        logger.error(f"Error executing retry call for {retry_entry['target_number']}: {e}")

async def send_admin_failure_log(campaign_id: int, failure_type: str, data: dict):
    """Send detailed failure information to admin log group."""
    try:
        if failure_type == "failed":
            message = (
                f"❌ <b>CALL FAILED</b>\n\n"
                f"<b>Campaign #{campaign_id}</b>\n"
                f"📱 {data.get('target_number', 'Unknown')}\n"
                f"⏱ Duration: {data.get('duration', 'Unknown')}\n"
                f"🔍 Cause: {data.get('cause', 'Unknown')}\n"
                f"🔄 Retry: {data.get('retry_count', 0)}/{MAX_RETRY_ATTEMPTS}\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}"
            )
        elif failure_type == "blocked":
            message = (
                f"🚫 <b>CALL BLOCKED</b>\n\n"
                f"<b>Campaign #{campaign_id}</b>\n"
                f"📱 {data.get('target_number', 'Unknown')}\n"
                f"⏱ Duration: {data.get('duration', 'Unknown')}\n"
                f"🛡️ Cause: {data.get('cause', 'Carrier blocked')}\n"
                f"🔄 Retry: {data.get('retry_count', 0)}/{MAX_RETRY_ATTEMPTS}\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}"
            )
        elif failure_type == "retry_queued":
            message = (
                f"🔄 <b>RETRY QUEUED</b>\n\n"
                f"<b>Campaign #{campaign_id}</b>\n"
                f"📱 {data.get('target_number', 'Unknown')}\n"
                f"🔄 Attempt: {data.get('retry_count', 0)}/{MAX_RETRY_ATTEMPTS}\n"
                f"⏰ Next attempt in {RETRY_DELAY_SECONDS}s"
            )
        elif failure_type == "max_retries":
            message = (
                f"⛔ <b>MAX RETRIES REACHED</b>\n\n"
                f"<b>Campaign #{campaign_id}</b>\n"
                f"📱 {data.get('target_number', 'Unknown')}\n"
                f"🔄 Final attempt: {MAX_RETRY_ATTEMPTS}/{MAX_RETRY_ATTEMPTS}\n"
                f"💀 Permanently failed"
            )
        else:
            return
            
        await global_application_instance.bot.send_message(
            chat_id=LOG_GROUP_ID,
            text=message,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Failed to send admin failure log: {e}")

async def send_individual_notification(campaign_id: int, notification_type: str, data: dict):
    """Send individual notification if enabled for campaign."""
    if campaign_id not in campaign_states:
        return
        
    campaign = campaign_states[campaign_id]
    if not campaign.individual_notifications:
        return
        
    # Get the chat_id where the campaign was initiated
    chat_id = campaign.user_id  # Default fallback
    if campaign_id in campaign_messages:
        chat_id = campaign_messages[campaign_id]["chat_id"]
    
    if notification_type == "dtmf_response":
        message = (
            f"🎯 <b>NEW VICTIM RESPONSE</b>\n\n"
            f"<b>Campaign #{campaign_id}</b>\n"
            f"📱 {data.get('target_number', 'Unknown')}\n"
            f"🔘 Pressed: {data.get('digit', '?')}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
    elif notification_type == "call_completed":
        message = (
            f"📞 <b>Call Completed</b>\n\n"
            f"<b>Campaign #{campaign_id}</b>\n"
            f"📱 {data.get('target_number', 'Unknown')}\n"
            f"⏱ Duration: {data.get('duration', 'Unknown')}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
    elif notification_type == "call_blocked":
        message = (
            f"🚫 <b>Call Blocked</b>\n\n"
            f"<b>Campaign #{campaign_id}</b>\n"
            f"📱 {data.get('target_number', 'Unknown')}\n"
            f"⏱ Duration: {data.get('duration', 'Unknown')}\n"
            f"🛡️ Cause: {data.get('cause', 'Carrier blocked')}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
    else:
        return
        
    try:
        await global_application_instance.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Failed to send individual notification: {e}")

def validate_phone_number(number: str) -> bool:
    """Validate phone number in E.164 format."""
    pattern = r'^\+[1-9]\d{1,14}$'
    return bool(re.match(pattern, number))

@asynccontextmanager
async def get_db_session():
    async with get_async_db_session() as session:
        try:
            yield session
        except Exception:
            raise

async def is_user_authorized(user_id: int) -> bool:
    """Check if a user is authorized to use the bot."""
    try:
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=user_id))
            agent = result.scalar_one_or_none()
            return agent is not None and agent.is_authorized
    except Exception as e:
        logger.error(f"Error checking user authorization: {e}")
        return False

async def check_authorization(update: Update, context: ContextTypes.DEFAULT_TYPE, silent_fail: bool = True) -> bool:
    """Check if user is authorized. In groups, silently ignore unauthorized users."""
    user = update.effective_user
    if not user:
        return False
    
    # Check if user is authorized
    authorized = await is_user_authorized(user.id)
    
    if not authorized:
        # In private chats, send error message
        # In group chats, silently ignore (to avoid spam)
        chat_type = update.effective_chat.type if update.effective_chat else "private"
        
        if chat_type == "private" and not silent_fail:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "❌ You are not authorized to use this bot. Please contact an administrator."
                )
        elif chat_type in ["group", "supergroup"]:
            # In groups, just log and ignore
            logger.info(f"Ignoring unauthorized user {user.id} (@{user.username}) in group {update.effective_chat.id}")
    
    return authorized

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    # Only send error messages in private chats to avoid group spam
    chat_type = update.effective_chat.type if update.effective_chat else "private"
    if chat_type == "private" and update.effective_message:
        error_message = "An error occurred while processing your request. Please try again later."
        await update.effective_message.reply_text(error_message)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: Agent) -> None:
    """Show the modern, overhauled main menu."""
    user_id = update.effective_user.id
    
    # Get user display name - username with @ prefix, or first name as fallback
    user_display = f"@{agent.username}" if agent.username else (update.effective_user.first_name or "User")
    
    # Build dynamic status indicators
    auth_status = "ACTIVE" if agent.is_authorized else "UNAUTHORIZED"
    auth_color = "🟢" if agent.is_authorized else "🔴"
    phone_status = agent.phone_number if agent.phone_number else "Not Set"
    
    # Route status - simplified to just MAIN/RED/BLACK
    route_status = "Not Set"
    route_emoji = "❌"
    if agent.route:
        route_map = {"M": "MAIN", "R": "RED", "B": "BLACK"}
        route_status = route_map.get(agent.route, "UNKNOWN")
        route_emoji = "🌐"
    
    # AutoDial status
    autodial_emoji = "🤖"
    autodial_status_text = "DISABLED"
    autodial_trunk_display = ""
    if agent.auto_dial:
        trunk_display = agent.autodial_trunk or "Default"
        autodial_status_text = f"ENABLED"
        autodial_trunk_display = f"<b>{autodial_status_text}</b> <i>({trunk_display.title()})</i>"
        autodial_emoji = "✅"
    else:
        autodial_trunk_display = f"<b>{autodial_status_text}</b>"
        autodial_emoji = "❌"
    
    # Caller ID status
    manual_cid = agent.caller_id or "Not Set"
    autodial_cid = agent.autodial_caller_id or "Not Set"
    
    # Build main action buttons for auto-dial only bot
    keyboard = []
    
    # Auto-Dial button (only if authorized - auto-enable if authorized)
    if agent.is_authorized:
        keyboard.append([
            InlineKeyboardButton("🤖 Auto-Dial Campaign", callback_data="auto_dial")
        ])
        
        # Campaign History button
        keyboard.append([
            InlineKeyboardButton("📊 Campaign History", callback_data="campaign_history")
        ])
    
    # Settings button (auto-dial settings only)
    keyboard.append([
        InlineKeyboardButton("⚙️ Auto-Dial Settings", callback_data="settings")
    ])
    
    # Manage Agents button (admin only)
    if update.effective_user.id == SUPER_ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton("👥 Manage Agents", callback_data="manage_agents")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Build the welcome message for auto-dial only bot
    welcome_message = (
        "🤖 <b><u>AUTO-DIAL BOT</u></b>\n\n"
        "👤 <b><u>USER</u></b>\n"
        f"└─ {user_display}\n\n"
        "🔐 <b><u>STATUS</u></b>\n"
        f"├─ {auth_color} Authorization: <b>{auth_status}</b>\n"
        f"└─ {autodial_emoji} AutoDial: {autodial_trunk_display}\n\n"
        "📲 <b><u>CAMPAIGN CALLER ID</u></b>\n"
        f"└─ <code>{autodial_cid}</code>\n\n"
        "~~~\n"
        "Available Commands:\n"
        "🤖 /autodial - Start auto-dial campaign\n"
        "🤖 /setautodialcid - Set campaign caller ID\n"
        "⚙️ /settings - Auto-dial settings (trunk selection)\n"
        "📊 /history - View campaign history\n"
        "ℹ️ /help - Show help"
    )

    # Send or edit the message
    if isinstance(update.callback_query, type(None)):
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.callback_query.message.edit_text(welcome_message, reply_markup=reply_markup, parse_mode='HTML')

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: Agent):
    """Displays the dynamic settings menu."""
    current_manual_route = agent.route if agent and agent.route else "Not Set"
    current_autodial_trunk = agent.autodial_trunk if agent and agent.autodial_trunk else "Not Set"

    keyboard = [
        [InlineKeyboardButton(f"📞 Auto-Dial Trunk ({current_autodial_trunk})", callback_data="select_autodial_trunk")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    settings_text = (
        "⚙️ *Auto-Dial Settings*\n\n"
        "Configure your auto-dial campaign settings:\n\n"
        f"• *Auto-Dial Trunk* - Choose trunk for campaigns ({current_autodial_trunk})\n"
        "• Use /setautodialcid to set campaign caller ID\n"
    )

    # Edit the message if called from a callback query, otherwise reply
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                settings_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
             logger.error(f"Error editing message in show_settings_menu: {e}")
             # Fallback reply if edit fails (e.g., message too old)
             if update.effective_message:
                  await update.effective_message.reply_text(settings_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.message: # Should not happen if called from buttons, but as fallback
        await update.message.reply_text(settings_text, reply_markup=reply_markup, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start command - shows the main menu."""
    try:
        user = update.effective_user
        
        async with get_db_session() as session:
            try:
                result = await session.execute(select(Agent).filter_by(telegram_id=user.id))
                agent = result.scalar_one_or_none()

                if not agent:
                    agent = Agent(
                        telegram_id=user.id,
                        username=user.username,
                        is_authorized=user.id == SUPER_ADMIN_ID
                    )
                    session.add(agent)
                    await session.commit()
                
                # Check authorization AFTER creating/finding agent record
                if not agent.is_authorized:
                    await update.message.reply_text(
                        "❌ You are not authorized to use this bot. Please contact an administrator.\n\n"
                        "Your account has been registered and is pending authorization."
                    )
                    return ConversationHandler.END
                
                await show_main_menu(update, context, agent)
                return MAIN_MENU
                
            except SQLAlchemyError as e:
                logger.error(f"Database error: {str(e)}")
                error_msg = "Error accessing database. Please try again later."
                if update.callback_query:
                    if update.callback_query.message:
                        try:
                            await update.callback_query.message.edit_text(error_msg)
                        except Exception as edit_e:
                            logger.error(f"Error editing message in start (SQLAlchemyError): {edit_e}")
                            await update.callback_query.message.reply_text(error_msg)
                elif update.message:
                    await update.message.reply_text(error_msg)
                return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in start command: {str(e)}")
        error_msg = "An error occurred. Please try again later."
        if update.callback_query:
            if update.callback_query.message:
                try:
                    await update.callback_query.message.edit_text(error_msg)
                except Exception as edit_e:
                    logger.error(f"Error editing message in start (General Exception): {edit_e}")
                    await update.callback_query.message.reply_text(error_msg)
        elif update.message:
            await update.message.reply_text(error_msg)
        return ConversationHandler.END

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu button presses."""
    query = update.callback_query
    await query.answer()
        
    agent = None # Initialize agent

    # Add handling for back_main first
    if query.data == "back_main":
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            if agent:
                await show_main_menu(update, context, agent)
            else:
                await query.message.edit_text("Error retrieving agent data.")
                return ConversationHandler.END
        return MAIN_MENU

    elif query.data == "refresh_menu":
        # Refresh the main menu with latest data
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            if agent:
                await show_main_menu(update, context, agent)
            else:
                await query.message.edit_text("Error retrieving agent data.")
                return ConversationHandler.END
        return MAIN_MENU

    elif query.data == "setup_wizard":
        # Guide user through initial setup
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            
            if not agent:
                await query.message.edit_text("Error: Agent not found.")
                return ConversationHandler.END
            
            # Determine what needs to be set up
            setup_steps = []
            if not agent.is_authorized:
                setup_steps.append("• Contact administrator for authorization")
            if not agent.phone_number:
                setup_steps.append("• Set phone number: `/setphone +1234567890`")
            if not agent.route:
                setup_steps.append("• Select a route in Settings")
            if not agent.caller_id:
                setup_steps.append("• Set caller ID: `/setcid +1234567890`")
            
            setup_text = (
                "🛠️ *Setup Wizard*\n\n"
                "Complete these steps to start making calls:\n\n"
                + "\n".join(setup_steps) + "\n\n"
                "Use the buttons below to configure your account:"
            )
            
            keyboard = [
                [InlineKeyboardButton("📱 Set Phone", callback_data="phone_number")],
                [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.edit_text(setup_text, reply_markup=reply_markup, parse_mode='Markdown')
        return MAIN_MENU

    elif query.data == "campaign_history":
        await query.message.edit_text(
            "📊 *Campaign History*\n\n"
            "Your campaign history will appear here.\n"
            "_(Feature coming soon)_\n\n"
            "• View past campaigns\n"
            "• Response rates\n"
            "• Call completion stats",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return MAIN_MENU
    
    elif query.data == "auto_dial":
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            if not agent or not agent.is_authorized:
                await query.message.edit_text(
                    "❌ You are not authorized to use the Auto-Dial feature. "
                    "Please contact an administrator for authorization.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
                    parse_mode='Markdown'
                )
                return MAIN_MENU
            
            if not agent.route:
                await query.message.edit_text(
                    "❌ No route configured. Please set your route first:\n\n"
                    "`/route one` or `/route two`\n\n"
                    "Route selection determines which trunk will be used for campaigns.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
                    parse_mode='Markdown'
                )
                return MAIN_MENU

        await query.message.edit_text(
            "🤖 *Auto-Dial Campaign*\n\n"
            "Please upload your .txt file containing phone numbers.\n\n"
            "File format requirements:\n"
            "• One phone number per line\n"
            "• E.164 format (e.g., +1234567890)\n"
            "• No empty lines or special characters (other than +)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return AUTO_DIAL

    elif query.data == "enable_autodial":
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            
            if not agent or not agent.is_authorized:
                await query.message.edit_text(
                    "❌ You are not authorized to enable Auto-Dial.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]])
                )
                return MAIN_MENU
            
            # Enable auto-dial for the agent
            agent.auto_dial = True
            await session.commit()
            
            await query.message.edit_text(
                "✅ *Auto-Dial Enabled*\n\n"
                "Auto-Dial feature has been enabled for your account.\n"
                "You can now start campaigns and configure trunks in Settings.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Configure Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
                ]),
                parse_mode='Markdown'
            )
        return MAIN_MENU

    elif query.data == "campaign_stats":
        # Show campaign statistics
        await query.message.edit_text(
            "📈 *Campaign Statistics*\n\n"
            "Your campaign performance data will appear here.\n"
            "_(Feature coming soon)_\n\n"
            "• Active campaigns\n"
            "• Response rates\n"
            "• Call completion stats",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return MAIN_MENU

    elif query.data == "profile":
        # Show agent profile
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            
            if not agent:
                await query.message.edit_text("Error: Agent not found.")
                return ConversationHandler.END
            
            profile_text = (
                "📱 *Agent Profile*\n\n"
                f"*Username:* @{agent.username or 'Not set'}\n"
                f"*Telegram ID:* `{agent.telegram_id}`\n"
                f"*Phone Number:* `{agent.phone_number or 'Not set'}`\n"
                f"*Authorization:* {'✅ Authorized' if agent.is_authorized else '❌ Unauthorized'}\n"
                f"*Manual Caller ID:* `{agent.caller_id or 'Not set'}`\n"
                f"*AutoDial Caller ID:* `{agent.autodial_caller_id or 'Not set'}`\n"
                f"*Route:* {agent.route or 'Not set'}\n"
                f"*AutoDial:* {'🟢 Enabled' if agent.auto_dial else '🔴 Disabled'}\n"
                f"*AutoDial Trunk:* {agent.autodial_trunk or 'Not set'}\n\n"
                "Use the buttons below to update your profile:"
            )
            
            keyboard = [
                [InlineKeyboardButton("📱 Update Phone", callback_data="phone_number")],
                [InlineKeyboardButton("📲 Set Caller ID", callback_data="set_caller_id")],
                [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.edit_text(profile_text, reply_markup=reply_markup, parse_mode='Markdown')
        return MAIN_MENU

    elif query.data == "set_caller_id":
        await query.message.edit_text(
            "📲 *Set Caller ID*\n\n"
            "To set your outbound caller ID, use:\n"
            "`/setcid <your_number>`\n\n"
            "Example: `/setcid +1234567890`\n\n"
            "• Use international format\n"
            "• Include country code\n"
            "• No spaces or special characters",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]]),
            parse_mode='Markdown'
        )
        return MAIN_MENU
    
# Removed phone number and call history handlers - auto-dial only
    
    elif query.data == "settings":
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            if not agent:
                await query.message.edit_text("Error: Agent not found. Please try /start again.")
                return ConversationHandler.END
            await show_settings_menu(update, context, agent)
        return SETTINGS

    elif query.data == "system_status" and update.effective_user.id == SUPER_ADMIN_ID:
        # Show system status (admin only)
        await query.message.edit_text(
            "🔧 *System Status*\n\n"
            "Checking system components...\n"
            "_(Use /status command for detailed info)_",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return MAIN_MENU
    
    elif query.data == "manage_agents" and update.effective_user.id == SUPER_ADMIN_ID:
        await show_agent_management_menu(update, context)
        return AGENT_MANAGEMENT

    elif query.data == "help":
        help_text = (
            "ℹ️ *Help & Commands*\n\n"
            "*Quick Commands:*\n"
            "📞 `/call +1234567890` - Make a call\n"
            "📱 `/setphone +1234567890` - Set phone\n"
            "📲 `/setcid +1234567890` - Set caller ID\n"
            "🌐 `/route M/R/B` - Set route\n"
            "🤖 `/autodial` - Start campaign\n"
            "⚙️ `/settings` - Open settings\n"
            "📊 `/status` - System status (admin)\n\n"
            "*Getting Started:*\n"
            "1. Set your phone number\n"
            "2. Choose a route\n"
            "3. Start making calls!\n\n"
            "*Need Support?*\n"
            "Contact your administrator for assistance."
        )
        
        await query.message.edit_text(
            help_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return MAIN_MENU

    # P1 Campaign Control Handlers
    elif query.data.startswith("pause_campaign_"):
        campaign_id = int(query.data.split("_")[-1])
        if campaign_id in campaign_states:
            campaign_states[campaign_id].is_paused = True
            await update_campaign_message(campaign_id)
            await query.answer("⏸ Campaign paused")
        return MAIN_MENU
    
    elif query.data.startswith("resume_campaign_"):
        campaign_id = int(query.data.split("_")[-1])
        if campaign_id in campaign_states:
            campaign_states[campaign_id].is_paused = False
            await update_campaign_message(campaign_id)
            await query.answer("▶️ Campaign resumed")
        return MAIN_MENU
    
    elif query.data.startswith("stop_campaign_"):
        campaign_id = int(query.data.split("_")[-1])
        if campaign_id in campaign_states:
            # Clean up campaign state
            del campaign_states[campaign_id]
            if campaign_id in campaign_messages:
                del campaign_messages[campaign_id]
            await query.message.edit_text(
                f"🛑 **Campaign #{campaign_id} Stopped**\n\n"
                "Campaign has been terminated and removed from memory.",
        parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")]])
            )
            await query.answer("🛑 Campaign stopped")
        return MAIN_MENU
    
    elif query.data.startswith("campaign_notifications_"):
        campaign_id = int(query.data.split("_")[-1])
        if campaign_id in campaign_states:
            # Toggle individual notifications
            campaign_states[campaign_id].individual_notifications = not campaign_states[campaign_id].individual_notifications
            status = "enabled" if campaign_states[campaign_id].individual_notifications else "disabled"
            await update_campaign_message(campaign_id)
            await query.answer(f"🔔 Individual notifications {status}")
        return MAIN_MENU
    
    elif query.data.startswith("campaign_details_"):
        campaign_id = int(query.data.split("_")[-1])
        if campaign_id in campaign_states:
            campaign = campaign_states[campaign_id]
            
            # Show different details based on user role
            if update.effective_user.id == SUPER_ADMIN_ID:
                # Admin view - show everything including hidden metrics
                details_text = (
                    f"🔧 **ADMIN: Campaign #{campaign_id} Details**\n\n"
                    f"**User-Facing Statistics:**\n"
                    f"├─ Total Calls: {campaign.total_calls}\n"
                    f"├─ Completed: {campaign.completed_calls}\n"
                    f"├─ Active: {campaign.active_calls}\n"
                    f"└─ DTMF Responses: {campaign.dtmf_responses}\n\n"
                    f"**Hidden Internal Metrics:**\n"
                    f"├─ ❌ Failed: {campaign.failed_calls}\n"
                    f"├─ 🚫 Blocked: {campaign.blocked_calls}\n"
                    f"├─ 🔄 Retry Queue: {len(campaign.retry_queue)}\n"
                    f"└─ 📈 Total Retries: {campaign.total_retries_processed}\n\n"
                    f"**Settings:**\n"
                    f"├─ Individual Notifications: {'✅ On' if campaign.individual_notifications else '❌ Off'}\n"
                    f"├─ Status: {'⏸ Paused' if campaign.is_paused else '🚀 Running'}\n"
                    f"└─ Started: {campaign.start_time.strftime('%H:%M:%S')}\n\n"
                    f"**Response Rate:** {(campaign.dtmf_responses / max(campaign.completed_calls, 1) * 100):.1f}%\n"
                    f"**Actual Completion:** {campaign.get_actual_completion_percentage()}%"
                )
            else:
                # User view - clean, stress-free metrics
                details_text = (
                    f"📊 **Campaign #{campaign_id} Details**\n\n"
                    f"**Statistics:**\n"
                    f"├─ Total Calls: {campaign.total_calls}\n"
                    f"├─ Completed: {campaign.completed_calls}\n"
                    f"├─ Active: {campaign.active_calls}\n"
                    f"└─ DTMF Responses: {campaign.dtmf_responses}\n\n"
                    f"**Settings:**\n"
                    f"├─ Individual Notifications: {'✅ On' if campaign.individual_notifications else '❌ Off'}\n"
                    f"├─ Status: {'⏸ Paused' if campaign.is_paused else '🚀 Running'}\n"
                    f"└─ Started: {campaign.start_time.strftime('%H:%M:%S')}\n\n"
                    f"**Response Rate:** {(campaign.dtmf_responses / max(campaign.completed_calls, 1) * 100):.1f}%"
                )
            
            await query.message.edit_text(
                details_text,
        parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Campaign", callback_data=f"back_campaign_{campaign_id}")]])
            )
        return MAIN_MENU
    
    elif query.data.startswith("back_campaign_"):
        campaign_id = int(query.data.split("_")[-1])
        if campaign_id in campaign_states:
            await update_campaign_message(campaign_id)
        return MAIN_MENU

    # Fallback for unknown callback data
    else:
        logger.warning(f"Unhandled callback data in MAIN_MENU: {query.data}")
        async with get_db_session() as session:
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
            agent = result.scalar_one_or_none()
            if agent:
                await show_main_menu(update, context, agent)
        return MAIN_MENU

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle settings menu interactions."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    agent = None # Define agent outside to potentially use in else block
    
    # Fetch agent once at the beginning if possible, but handle cases where it might need re-fetching
    # Initial fetch to reduce redundant queries in simple cases
    async with get_db_session() as initial_session:
        result = await initial_session.execute(select(Agent).filter_by(telegram_id=user_id))
        agent = result.scalar_one_or_none()
        if not agent: # Check agent existence early
            await query.message.edit_text("Error: Agent data not found. Please use /start again.")
            return ConversationHandler.END

    # We use the 'agent' object fetched above for most branches
    # Re-fetch inside a new session context only when modifications are needed

    if query.data == "back_main":
        await show_main_menu(update, context, agent) # Use existing show_main_menu with the fetched agent
        return MAIN_MENU
            
    # Route selection removed for auto-dial only bot
        
    # --- Auto-Dial Trunk Selection --- 
    elif query.data == "select_autodial_trunk":
        # Use the initially fetched agent for authorization check
        if not agent.is_authorized:
            await query.message.edit_text("❌ You are not authorized to change settings.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_settings")]])) 
            return SETTINGS
                
        # No DB interaction here, just display options
        keyboard = [
            [InlineKeyboardButton("1️⃣ AutoDial One", callback_data="autodialtrunk_one")],
            [InlineKeyboardButton("2️⃣ AutoDial Two", callback_data="autodialtrunk_two")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "📞 *Select Auto-Dial Trunk*\n\n"
            "Choose the trunk for Auto-Dial campaigns:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SETTINGS
            
    elif query.data.startswith("autodialtrunk_"): 
        selected_trunk = query.data.split("_")[-1] # Should be 'one' or 'two'
        trunk_name_map = {'one': 'AutoDial One', 'two': 'AutoDial Two'}
        trunk_display_name = trunk_name_map.get(selected_trunk, 'Unknown')

        # Use initially fetched agent for auth check
        if not agent.is_authorized:
            await query.message.edit_text("❌ You are not authorized to set configuration.")
            return SETTINGS
        
        # Need a session to update the DB
        async with get_db_session() as session:
            try:
                # Fetch agent again within this transaction to ensure it's bound
                result = await session.execute(select(Agent).filter_by(telegram_id=user_id))
                agent_to_update = result.scalar_one_or_none()

                if not agent_to_update:
                     await query.message.edit_text("Error: Agent not found during trunk update.")
                     return SETTINGS # Or END?

                # Update trunk in the fetched DB object
                agent_to_update.autodial_trunk = selected_trunk
                session.add(agent_to_update) # Add instance to session
                await session.commit() # Commit the change
                await session.refresh(agent_to_update) # Refresh to get latest state if needed
                agent = agent_to_update # Update outer agent variable for UI refresh

                # Edit the current message to show confirmation briefly
                confirmation_text = f"✅ Auto-Dial Trunk set to *{trunk_display_name}*"
                await query.message.edit_text(
                     confirmation_text, 
                     parse_mode='Markdown'
                )
                # Pause briefly (optional)
                await asyncio.sleep(1.5) 
                
                # Show updated settings menu (will edit the message again)
                await show_settings_menu(update, context, agent) # await helper

            except SQLAlchemyError as e:
                logger.error(f"Database error saving autodial_trunk: {str(e)}")
                await session.rollback()
                await query.message.edit_text(
                    "❌ Error saving trunk selection. Please try again.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_settings")]])
                )
                # Fall through to return SETTINGS
            except Exception as e: # Catch other potential errors
                 logger.error(f"Unexpected error setting autodial trunk: {str(e)}")
                 await query.message.edit_text("An unexpected error occurred.")
                 # Fall through to return SETTINGS
                 
        return SETTINGS # Always return SETTINGS after handling trunk selection
            
    # --- End Auto-Dial Trunk Selection ---
        
    elif query.data == "back_settings":
        # Just show the settings menu using the helper and initially fetched agent
        await show_settings_menu(update, context, agent) # await helper
        return SETTINGS
            
    # Fallback if unknown callback in Settings
    else:
        logger.warning(f"Unhandled callback data in SETTINGS: {query.data}")
        # Show settings menu again using the helper and initially fetched agent
        await show_settings_menu(update, context, agent) # await helper
        return SETTINGS

async def handle_phone_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle phone settings menu interactions."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_main":
        # Need to fetch agent to show main menu
        async with get_db_session() as session:
             result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
             agent = result.scalar_one_or_none()
             if agent:
                 await show_main_menu(update, context, agent)
             else:
                 # Handle agent not found case
                 if query.message:
                      await query.message.edit_text("Error: Agent data not found.")
        return MAIN_MENU
            
    # If other actions were added to phone settings, handle them here
    return PHONE_SETTINGS

async def handle_call_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle call menu interactions."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_main":
        # Need to fetch agent to show main menu
        async with get_db_session() as session:
             result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
             agent = result.scalar_one_or_none()
             if agent:
                 await show_main_menu(update, context, agent)
             else:
                  if query.message:
                     await query.message.edit_text("Error: Agent data not found.")
        return MAIN_MENU
            
    # If other actions were added to call menu, handle them here
    return CALL_MENU

async def show_agent_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the agent management menu."""
    keyboard = [
        [InlineKeyboardButton("👥 List All Agents", callback_data="list_agents")],
        [InlineKeyboardButton("✅ Authorize Agent", callback_data="authorize_agent")],
        [InlineKeyboardButton("❌ Deauthorize Agent", callback_data="deauthorize_agent")],
        [InlineKeyboardButton("🗑️ Delete Agent", callback_data="delete_agent")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    menu_text = (
        "👥 *Agent Management*\n\n"
        "Manage your call center agents:\n\n"
        "• View all agents\n"
        "• Authorize new agents\n"
        "• Manage permissions\n"
        "• View agent statistics\n\n"
        "Select an option:"
    )
    
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.message:
        await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_agent_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle agent management menu interactions."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_main":
        # Need to fetch agent to show main menu
        async with get_db_session() as session:
             result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
             agent = result.scalar_one_or_none()
             if agent:
                 await show_main_menu(update, context, agent)
             else:
                 if query.message:
                     await query.message.edit_text("Error: Agent data not found.")
        return MAIN_MENU
            
    if update.effective_user.id != SUPER_ADMIN_ID:
        await query.message.edit_text("❌ Unauthorized access.")
        return MAIN_MENU # Return to main menu if not admin
    
    # Handle cancel for authorize/deauthorize
    if query.data == "cancel_authorize" or query.data == "cancel_deauthorize":
        # Just clear user data - no need to remove handler as we're using ConversationHandler states now
        context.user_data.pop("awaiting_agent_action", None)
        await show_agent_management_menu(update, context)
        return AGENT_MANAGEMENT

    # Show agent management menu if the callback is "manage_agents"
    if query.data == "manage_agents": 
        await show_agent_management_menu(update, context)
        return AGENT_MANAGEMENT
        
    # List all agents
    elif query.data == "list_agents":
        async with get_db_session() as session:
            result = await session.execute(select(Agent))
            agents = result.scalars().all()
            
            if not agents:
                await query.message.edit_text(
                    "No agents found in the database.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cancel_authorize")]])
                )
                return AGENT_MANAGEMENT
            
            agent_list = "👥 *All Agents*\n\n"
            for agent in agents:
                status = "✅ Authorized" if agent.is_authorized else "❌ Unauthorized"
                phone = f"`{agent.phone_number}`" if agent.phone_number else "Not set"
                username = agent.username or 'None'
                # Escape markdown characters in username
                username_escaped = username.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('`', '\\`').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('=', '\\=').replace('|', '\\|').replace('{', '\\{').replace('}', '\\}').replace('.', '\\.').replace('!', '\\!')
                agent_list += f"*ID:* `{agent.telegram_id}`\n*Username:* @{username_escaped}\n*Status:* {status}\n*Phone:* {phone}\n\n"
            
            # Add a back button
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="manage_agents")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.edit_text(agent_list, reply_markup=reply_markup, parse_mode='Markdown')
            return AGENT_MANAGEMENT
    
    # Authorize an agent
    elif query.data == "authorize_agent":
        # Store that we're waiting for an agent ID to authorize
        context.user_data["awaiting_agent_action"] = "authorize"
        
        await query.message.edit_text(
            "✅ *Authorize Agent*\n\n"
            "Please enter the Telegram ID of the agent you want to authorize.\n"
            "Example: `123456789`\n\n"
            "The agent must have used the bot at least once.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Cancel", callback_data="cancel_authorize")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
            ]),
            parse_mode='Markdown'
        )
        
        # No longer register a temporary handler - it will be handled by the ConversationHandler
        return AGENT_ID_INPUT
    
    # Deauthorize an agent
    elif query.data == "deauthorize_agent":
        # Store that we're waiting for an agent ID to deauthorize
        context.user_data["awaiting_agent_action"] = "deauthorize"
        
        await query.message.edit_text(
            "❌ *Deauthorize Agent*\n\n"
            "Please enter the Telegram ID of the agent you want to deauthorize.\n"
            "Example: `123456789`\n\n"
            "This will revoke their access to the bot's features.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Cancel", callback_data="cancel_deauthorize")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
            ]),
            parse_mode='Markdown'
        )
        
        # No longer register a temporary handler - it will be handled by the ConversationHandler
        return AGENT_ID_INPUT
    
    # Default: show agent management menu
    await show_agent_management_menu(update, context)
    return AGENT_MANAGEMENT

async def handle_agent_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle agent ID input for authorization/deauthorization."""
    
    # Handle callback queries (buttons) first
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        # Handle cancel buttons
        if query.data in ("cancel_authorize", "cancel_deauthorize"):
            context.user_data.pop("awaiting_agent_action", None)
            await show_agent_management_menu(update, context)
            return AGENT_MANAGEMENT
            
        # Handle back to main menu
        elif query.data == "back_main":
            context.user_data.pop("awaiting_agent_action", None)
            async with get_db_session() as session:
                result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id))
                agent = result.scalar_one_or_none()
                if agent:
                    await show_main_menu(update, context, agent)
                else:
                    await query.message.edit_text("Error: Agent data not found.")
            return MAIN_MENU
    
    # If not a callback query, it must be a text message with the agent ID
    # Get the action type (authorize or deauthorize)
    action = context.user_data.get("awaiting_agent_action")
    if not action:
        await update.message.reply_text("Error: No pending agent action found.")
        return MAIN_MENU
    
    # Clear the awaiting action
    del context.user_data["awaiting_agent_action"]
    
    # Get the agent ID from the message
    try:
        agent_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid agent ID format. Please use only numbers.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="cancel_authorize")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
            ])
        )
        return AGENT_ID_INPUT
    
    # Process the agent ID
    async with get_db_session() as session:
        # Check if the agent exists
        result = await session.execute(select(Agent).filter_by(telegram_id=agent_id))
        agent = result.scalar_one_or_none()
        
        if not agent:
            await update.message.reply_text(
                f"❌ Agent with ID {agent_id} not found. The agent must use the bot at least once.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="cancel_authorize")],
                    [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
                ])
            )
            return AGENT_ID_INPUT
        
        # Update the agent's authorization status
        if action == "authorize":
            # Don't change if already authorized
            if agent.is_authorized:
                username_display = agent.username or str(agent_id)
                await update.message.reply_text(
                    f"ℹ️ Agent @{username_display} is already authorized.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="manage_agents")]])
                )
                return AGENT_MANAGEMENT
            
            agent.is_authorized = True
            await session.commit()
            
            username_display = agent.username or str(agent_id)
            await update.message.reply_text(
                f"✅ Agent @{username_display} has been successfully authorized!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cancel_authorize")]])
            )
            
        elif action == "deauthorize":
            # Don't change if already unauthorized
            if not agent.is_authorized:
                username_display = agent.username or str(agent_id)
                await update.message.reply_text(
                    f"ℹ️ Agent @{username_display} is already unauthorized.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cancel_deauthorize")]])
                )
                return AGENT_MANAGEMENT
            
            # Don't allow deauthorizing the super admin
            if agent.telegram_id == SUPER_ADMIN_ID:
                await update.message.reply_text(
                    "❌ Cannot deauthorize the super admin!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cancel_deauthorize")]])
                )
                return AGENT_MANAGEMENT
            
            agent.is_authorized = False
            await session.commit()
            
            username_display = agent.username or str(agent_id)
            await update.message.reply_text(
                f"❌ Agent @{username_display} has been deauthorized.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cancel_deauthorize")]])
            )
    
    return AGENT_MANAGEMENT


async def set_autodial_caller_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's outbound caller ID specifically for Auto-Dial campaigns."""
    user = update.effective_user
    if not user:
        return

    # Fetch agent data
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async query
        agent = result.scalar_one_or_none()

        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return
            
        # Check authorization AFTER finding agent
        if not agent.is_authorized:
            await update.message.reply_text("❌ You are not authorized to use this bot. Please contact an administrator.")
            return

        # Argument parsing (no DB change)
        if not context.args:
            current_cid = agent.autodial_caller_id or "Not set"
            await update.message.reply_text(
                f"🤖 *Set Auto-Dial CallerID*\n\n"
                f"Current Auto-Dial CID: `{current_cid}`\n\n"
                "Please provide the phone number to use for Auto-Dial campaigns:\n"
                "`/setautodialcid +1234567890`\n\n"
                "• Must be E.164 format\n"
                "• If not set, Auto-Dial may fail or use a default.", # Add clarification
                parse_mode='Markdown'
            )
            return

        autodial_caller_id = context.args[0]
        
        # Validation (no DB change)
        if not validate_phone_number(autodial_caller_id):
            await update.message.reply_text(
                "❌ Invalid phone number format.\n\n"
                "Please use E.164 format:\n"
                "Example: `/setautodialcid +1234567890`",
                parse_mode='Markdown'
            )
            return

        # Agent already fetched within this session
        try:
            # Update autodial_caller_id
            agent.autodial_caller_id = autodial_caller_id
            session.add(agent) # Add for update tracking
            # await session.commit() # Commit handled by context manager
            
            await update.message.reply_text(
                "✅ Auto-Dial CallerID updated successfully!\n\n"
                f"🤖 New Auto-Dial CID: `{autodial_caller_id}`",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in set_autodial_caller_id: {str(e)}")
            # await session.rollback() # Rollback handled by context manager
            await update.message.reply_text("❌ Error updating Auto-Dial caller ID. Please try again later.")

async def set_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's route (one or two) for auto-dial campaigns."""
    user = update.effective_user
    if not user:
        return

    async with get_db_session() as session:
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id))
        agent = result.scalar_one_or_none()

        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return
            
        # Check authorization AFTER finding agent
        if not agent.is_authorized:
            await update.message.reply_text("❌ You are not authorized to use this bot. Please contact an administrator.")
            return

        if not context.args:
            current_route = agent.route or "Not set"
            await update.message.reply_text(
                f"🌐 *Set Route*\n\n"
                f"Current Route: `{current_route}`\n\n"
                "Please specify which route to use:\n"
                "`/route one` - Use Route One\n"
                "`/route two` - Use Route Two\n\n"
                "• Required for auto-dial campaigns\n"
                "• Determines which trunk will be used",
                parse_mode='Markdown'
            )
            return

        route_arg = context.args[0].lower()
        
        if route_arg not in ['one', 'two']:
            await update.message.reply_text(
                "❌ Invalid route. Please use:\n"
                "`/route one` or `/route two`",
                parse_mode='Markdown'
            )
            return

        try:
            # Update route
            agent.route = route_arg
            # Auto-enable autodial when route is set
            agent.auto_dial = True
            # Set corresponding autodial trunk
            agent.autodial_trunk = route_arg
            session.add(agent)
            await session.commit()
            
            await update.message.reply_text(
                f"✅ Route updated successfully!\n\n"
                f"🌐 Route: `{route_arg.title()}`\n"
                f"🤖 Auto-Dial: `Enabled`\n"
                f"📞 Trunk: `autodial-{route_arg}`\n\n"
                "You can now start auto-dial campaigns!",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in set_route: {str(e)}")
            await update.message.reply_text("❌ Error updating route. Please try again later.")

async def check_ami_status(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if AMI is connected and working."""
    ami_manager = context.application.bot_data.get("ami_manager")
    
    if not ami_manager:
        return False
        
    try:
        response = await ami_manager.send_action({'Action': 'Ping'})
        return response and response.get('Response') == 'Success'
    except Exception:
        return False

async def check_trunk_status(context: ContextTypes.DEFAULT_TYPE, trunk_name: str) -> dict:
    """Check registration status of a trunk."""
    ami_manager = context.application.bot_data.get("ami_manager")
    
    if not ami_manager:
        return {'status': 'Unknown', 'error': 'AMI not connected'}
        
    try:
        response = await ami_manager.send_action({
            'Action': 'PJSIPShowEndpoint',
            'Endpoint': trunk_name
        })
        return {'status': 'Registered', 'details': response}
    except Exception as e:
        return {'status': 'Error', 'error': str(e)}

async def get_asterisk_status(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Get Asterisk system status."""
    ami_manager = context.application.bot_data.get("ami_manager")
    
    if not ami_manager:
        return {'status': 'Error', 'error': 'AMI not connected'}
        
    try:
        uptime = await ami_manager.send_action({'Action': 'CoreStatus'})
        channels = await ami_manager.send_action({'Action': 'CoreShowChannels'})
        return {
            'status': 'OK',
            'uptime': uptime,
            'channels': channels
        }
    except Exception as e:
        return {'status': 'Error', 'error': str(e)}

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system status - Admin only command."""
    user = update.effective_user
    if not user:
        return
        
    # Check if user is super admin directly
    if user.id != SUPER_ADMIN_ID:
        await update.message.reply_text(
            "❌ *Unauthorized Access*\n\n"
            "This command is only available to administrators.",
            parse_mode='Markdown'
        )
        return

    # Send initial message
    status_message = await update.message.reply_text(
        "🔄 *Checking System Status*\n\n"
        "Please wait while I gather information...",
        parse_mode='Markdown'
    )

    try:
        # Check AMI Connection
        ami_status = "✅ Connected" if await check_ami_status(context) else "❌ Disconnected"
        
        # Check Trunk Status
        main_trunk = await check_trunk_status(context, "main-trunk")
        dev_trunk = await check_trunk_status(context, "dev-trunk")
        
        # Get Asterisk Status
        asterisk_status = await get_asterisk_status(context)
        
        # Format trunk status
        main_status = "✅ Registered" if main_trunk['status'] == 'Registered' else "❌ Not Registered"
        dev_status = "✅ Registered" if dev_trunk['status'] == 'Registered' else "❌ Not Registered"
        
        # Build status message
        status_text = (
            "🎯 *Siren Call Center Status*\n"
            "─────────────────────\n\n"
            "*AMI Connection*\n"
            f"Status: {ami_status}\n"
            f"Host: `{AMI_HOST}:{AMI_PORT}`\n\n"
            "*Trunk Status*\n"
            f"Main Trunk: {main_status}\n"
            f"Dev Trunk: {dev_status}\n\n"
        )
        
        # Add Asterisk status if available
        if asterisk_status['status'] == 'OK':
            uptime = asterisk_status.get('uptime', {}).get('CoreUptime', 'Unknown')
            channels = asterisk_status.get('channels', {}).get('ListItems', [])
            active_calls = len(channels) if isinstance(channels, list) else 0
            
            status_text += (
                "*Asterisk Status*\n"
                f"Uptime: `{uptime}`\n"
                f"Active Calls: `{active_calls}`\n\n"
            )
        
        # Add timestamp
        status_text += (
            "─────────────────────\n"
            f"Last Updated: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
        )
        
        # Update status message
        await status_message.edit_text(
            status_text,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in status command: {str(e)}")
        await status_message.edit_text(
            "❌ *Error Checking Status*\n\n"
            "An error occurred while checking system status.\n"
            f"Error: `{str(e)}`",
            parse_mode='Markdown'
        )

# Manual calling functionality removed - auto-dial only bot

async def post_init(application: Application) -> None:
    global global_application_instance
    """Post initialization hook for the bot to set up AMI connection and listener."""
    try:
        # Initialize AMI connection using PTB's event loop
        ami_manager = Manager(
            host=AMI_HOST,
            port=AMI_PORT,
            username=AMI_USERNAME,
            secret=AMI_SECRET,
            encoding='utf8'
        )
        logger.info("Connecting to Asterisk AMI...")
        await ami_manager.connect()
        logger.info("Successfully connected to Asterisk AMI")

        # Send a test message to the user to verify notification capability
        test_user_id = 7991166259  # Hardcoded for testing, based on logs
        test_message = "🔔 *Test Notification*\n\nThis is a test message to confirm that the bot can send notifications to you."
        try:
            await application.bot.send_message(
                chat_id=test_user_id,
                text=test_message,
                parse_mode='Markdown'
            )
            logger.info(f"Test notification sent to user {test_user_id}")
        except Exception as e:
            logger.error(f"Failed to send test notification to user {test_user_id}: {e}")

        # Keep the existing DTMFEnd listener - it already works
        
        # Newchannel event listener to map Uniqueid to call_id using database
        async def new_channel_event_listener(manager, event):
            # Log all the event fields at debug level
            event_dict = dict(event)
            logger.debug("=== Newchannel Event Fields ===")
            for key, value in event_dict.items():
                logger.debug(f"{key}: {value}")
            logger.debug("==============================")
            
            uniqueid = event.get('Uniqueid')
            channel = event.get('Channel')
            exten = event.get('Exten')
            context = event.get('Context')
            call_id_from_event = event.get('CallID')  # Check if CallID is passed in event
            
            logger.info(f"Processing Newchannel event: Uniqueid={uniqueid}, Channel={channel}, Exten={exten}, Context={context}, CallID={call_id_from_event}")
            
            # Check if this is a new outbound call (either from autodial-ivr or manual call from main trunk)
            if context in ['autodial-ivr', 'from-main-trunk']:
                try:
                    async with get_async_db_session() as session:
                        # Try finding the call using different methods - prioritize uniqueid which is often our call_id
                        call = None
                        
                        if uniqueid:
                            # First try by Asterisk Uniqueid which often contains our call_id
                            call = await Call.find_by_uniqueid(session, uniqueid)
                            if not call:
                                # If not found directly, try using uniqueid as call_id (they're often the same)
                                call = await Call.find_by_call_id(session, uniqueid)
                            if call:
                                logger.info(f"Found call in database by Uniqueid/CallID: {uniqueid}")
                        
                        if not call and call_id_from_event:
                            # Then try by call_id from event variables
                            call = await Call.find_by_call_id(session, call_id_from_event)
                            if call:
                                logger.info(f"Found call {call_id_from_event} by CallID variable from event")
                        
                        # If not found by call_id, try to match by target number from channel name
                        if not call and channel and 'PJSIP/' in channel:
                            # Extract the target number from the channel name (e.g., PJSIP/1234567890@trunk)
                            try:
                                target_part = channel.split('PJSIP/')[1].split('@')[0]
                                if target_part.isdigit() or (target_part.startswith('+') and target_part[1:].isdigit()):
                                    target_number = target_part
                                    call = await Call.find_latest_by_target(session, target_number)
                                    if call:
                                        logger.info(f"Found call {call.call_id} by matching target number {target_number} from channel")
                            except (IndexError, AttributeError):
                                pass
                        
                        # If still not found, try to find the most recent pending call
                        if not call:
                            call = await Call.find_latest_pending(session)
                            if call:
                                logger.info(f"Found latest pending call {call.call_id} with no uniqueid/channel")
                        
                        # If a call was found, update it with the uniqueid and channel
                        if call:
                            # Update the call with Uniqueid and actual channel
                            original_status = call.status
                            call.uniqueid = uniqueid
                            call.channel = channel
                            
                            # Only update to connected if we're not already in a connected state
                            if call.status != "connected":
                                call.status = "connected"
                                call.call_metadata = {
                                    **(call.call_metadata or {}),
                                    "connected_time": datetime.now().isoformat(),
                                    "asterisk_context": context,
                                    "asterisk_exten": exten,
                                    "last_status_update": datetime.now().isoformat()
                                }
                                
                                # Update the existing status message if available
                                if call.agent_telegram_id and global_application_instance:
                                    # Check if we have a stored status message ID
                                    status_message_id = call.call_metadata.get('status_message_id')
                                    status_chat_id = call.call_metadata.get('status_chat_id')
                                    
                                    if status_message_id and status_chat_id:
                                        # Update the existing status message
                                        try:
                                            await global_application_instance.bot.edit_message_text(
                                                chat_id=status_chat_id,
                                                message_id=status_message_id,
                                                text=f"📞 *Call Status*\n\n"
                                                     f"• *Agent:* `{call.agent_phone or 'Unknown'}`\n"
                                                     f"• *Target:* `{call.target_number or 'Unknown'}`\n"
                                                     f"• *Route:* {call.route or 'Default'} Route\n"
                                                     f"• *Status:* Connecting to target...\n\n"
                                                     f"_Step 1: ✓ Agent answered_\n"
                                                     f"_Step 2: Dialing target number..._\n\n"
                                                     f"_Please wait while we connect your call._",
                                                parse_mode='Markdown'
                                            )
                                            logger.info(f"Updated status message {status_message_id} for call {call.call_id} - agent answered")
                                        except Exception as e:
                                            logger.error(f"Failed to update status message: {e}")
                                    else:
                                        # No stored message ID, send a new message
                                        logger.warning(f"No status message ID found for call {call.call_id}, sending new status update")
                                        await global_application_instance.bot.send_message(
                                            chat_id=call.agent_telegram_id,
                                            text=f"🔊 *Call Status Update*\n\n"
                                                 f"• *To:* `{call.target_number or 'Unknown'}`\n"
                                                 f"• *Status:* Connecting to target...\n"
                                                 f"• *Time:* {datetime.now().strftime('%H:%M:%S')}",
                                            parse_mode='Markdown'
                                        )
                            await session.commit()
                            
                            logger.info(f"Updated call {call.call_id} with uniqueid={uniqueid}, channel={channel}")
                            logger.info(f"Call status changed from {original_status} to connected")
                            
                            # Log detailed call info for debugging
                            logger.debug(f"Call details: Target={call.target_number}, Campaign={call.campaign_id}, Agent={call.agent_telegram_id}")
                            
                            # ICM is now handled by bridge_event_listener when target answers
                            # This ensures we only show ICM when call is actually connected to target
                            # DO NOT DISPLAY ICM HERE - ONLY IN BRIDGE EVENT
                        else:
                            # No matching call found in database, create a new record if needed
                            logger.warning(f"No matching call found in database for channel {channel} and Uniqueid {uniqueid}")
                            
                            # Extract target number from channel if possible
                            target_number = None
                            if channel and 'PJSIP/' in channel:
                                try:
                                    target_part = channel.split('PJSIP/')[1].split('@')[0]
                                    if target_part.isdigit() or (target_part.startswith('+') and target_part[1:].isdigit()):
                                        target_number = target_part
                                except (IndexError, AttributeError):
                                    pass
                            
                            # Optionally create a new call record for unknown calls
                            # This is for debugging purposes - we can track all calls even if not initiated by us
                            if target_number:
                                new_call_id = f"unknown_{uniqueid}_{int(time.time())}"
                                new_call = Call(
                                    call_id=new_call_id,
                                    target_number=target_number,
                                    uniqueid=uniqueid,
                                    channel=channel,
                                    status="unknown_origin",
                                    start_time=datetime.now(),
                                    call_metadata={
                                        "context": context,
                                        "exten": exten,
                                        "detected_time": datetime.now().isoformat(),
                                        "origin": "external"
                                    }
                                )
                                session.add(new_call)
                                await session.commit()
                                logger.info(f"Created new record for unknown call: {new_call_id} with target {target_number}")
                except Exception as e:
                    logger.error(f"Error processing Newchannel event: {str(e)}")
            else:
                logger.debug(f"Ignoring non-autodial channel: {channel} (Context: {context})")


        # Register event listeners - DTMFEnd captures the actual digit pressed
        ami_manager.register_event('Newchannel', new_channel_event_listener)
        ami_manager.register_event('Newstate', newstate_event_listener)  # SIP state tracking
        ami_manager.register_event('DialBegin', dial_begin_event_listener)  # Dial attempt tracking
        ami_manager.register_event('DialEnd', dial_end_event_listener)  # Dial result tracking
        ami_manager.register_event('DTMFBegin', dtmf_begin_listener)  # DTMF start detection
        ami_manager.register_event('DTMFEnd', dtmf_event_listener)  # DTMF digit capture and notification
        ami_manager.register_event('Hangup', hangup_event_listener) # Register Hangup event listener
        ami_manager.register_event('BridgeEnter', bridge_event_listener) # Register Bridge event listener for ICM
        logger.info("AMI event listeners registered.")

        # Store in application context for access in handlers
        application.bot_data["ami_manager"] = ami_manager
        global_application_instance = application # Store application instance globally
        
        # Start retry queue processor
        asyncio.create_task(retry_queue_processor())

    except Exception as e:
        logger.error(f"Failed to establish AMI connection or register listener: {str(e)}")
        application.bot_data["ami_manager"] = None

async def newstate_event_listener(manager, event):
    """Handle Newstate events to track real-time channel state changes."""
    # Log event details for debugging
    event_dict = dict(event)
    logger.debug(f"Newstate Event: {event_dict}")
    
    uniqueid = event.get('Uniqueid')
    channel = event.get('Channel')
    channel_state = event.get('ChannelState')
    channel_state_desc = event.get('ChannelStateDesc')
    
    if not uniqueid or not channel:
        return
        
    logger.info(f"Newstate: Channel={channel}, State={channel_state} ({channel_state_desc}), UniqueID={uniqueid}")
    
    try:
        async with get_async_db_session() as session:
            # Find the call by uniqueid
            call = await Call.find_by_uniqueid(session, uniqueid)
            if not call:
                # Try finding by channel if uniqueid lookup fails
                call = await Call.find_by_channel(session, channel)
                
            if call:
                # Update call metadata with state information
                current_metadata = call.call_metadata or {}
                state_history = current_metadata.get('state_history', [])
                
                # Add new state to history
                state_entry = {
                    "time": datetime.now().isoformat(),
                    "state": channel_state,
                    "state_desc": channel_state_desc,
                    "channel": channel
                }
                state_history.append(state_entry)
                
                # Update call metadata
                call.call_metadata = {
                    **current_metadata,
                    "state_history": state_history,
                    "current_state": channel_state,
                    "current_state_desc": channel_state_desc,
                    "last_state_update": datetime.now().isoformat()
                }
                
                # Update campaign state based on channel state
                campaign_id = call.campaign_id
                if campaign_id and isinstance(campaign_id, int) and campaign_id in campaign_states:
                    # Channel state meanings:
                    # 0=Down, 1=Rsrvd, 2=OffHook, 3=Dialing, 4=Ring, 5=Ringing, 6=Up, 7=Busy, 8=Dialing Offhook, 9=Pre-ring
                    
                    if channel_state == '4' or channel_state == '5':  # Ring or Ringing
                        logger.info(f"Campaign {campaign_id}: Call {call.call_id} is RINGING (real carrier response)")
                        # This indicates the call is actually ringing at the target
                        
                    elif channel_state == '6':  # Up (answered)
                        logger.info(f"Campaign {campaign_id}: Call {call.call_id} is UP (answered)")
                        # Call has been answered - this is a real connection
                        
                    elif channel_state == '7':  # Busy
                        logger.info(f"Campaign {campaign_id}: Call {call.call_id} is BUSY")
                        # Busy signal - legitimate failure
                
                await session.commit()
                logger.debug(f"Updated call {call.call_id} with state {channel_state_desc}")
            else:
                logger.debug(f"No call found for Newstate event: UniqueID={uniqueid}, Channel={channel}")
                
    except Exception as e:
        logger.error(f"Error processing Newstate event: {e}", exc_info=True)

async def dial_begin_event_listener(manager, event):
    """Handle DialBegin events to track when dial attempts start."""
    event_dict = dict(event)
    logger.debug(f"DialBegin Event: {event_dict}")
    
    uniqueid = event.get('Uniqueid')
    dest_uniqueid = event.get('DestUniqueID')
    channel = event.get('Channel')
    destination = event.get('Destination')
    
    logger.info(f"DialBegin: Channel={channel}, Destination={destination}, UniqueID={uniqueid}, DestUniqueID={dest_uniqueid}")
    
    try:
        async with get_async_db_session() as session:
            # Find the call by uniqueid (could be either source or dest)
            call = await Call.find_by_uniqueid(session, uniqueid)
            if not call and dest_uniqueid:
                call = await Call.find_by_uniqueid(session, dest_uniqueid)
                
            if call:
                # Update call metadata with dial begin information
                current_metadata = call.call_metadata or {}
                call.call_metadata = {
                    **current_metadata,
                    "dial_begin": {
                        "time": datetime.now().isoformat(),
                        "destination": destination,
                        "dest_uniqueid": dest_uniqueid
                    }
                }
                
                # Update call status to indicate dialing has started
                if call.status in ['queued', 'initiated', 'sending']:
                    call.status = 'dialing'
                
                await session.commit()
                logger.info(f"Updated call {call.call_id} - dial attempt started to {destination}")
            else:
                logger.debug(f"No call found for DialBegin event: UniqueID={uniqueid}")
                
    except Exception as e:
        logger.error(f"Error processing DialBegin event: {e}", exc_info=True)

async def dial_end_event_listener(manager, event):
    """Handle DialEnd events to track dial status for classification."""
    event_dict = dict(event)
    logger.debug(f"DialEnd Event: {event_dict}")
    
    uniqueid = event.get('Uniqueid')
    dest_uniqueid = event.get('DestUniqueID')
    channel = event.get('Channel')
    dial_status = event.get('DialStatus')
    
    logger.info(f"DialEnd: Channel={channel}, DialStatus={dial_status}, UniqueID={uniqueid}, DestUniqueID={dest_uniqueid}")
    
    try:
        async with get_async_db_session() as session:
            # Find the call by uniqueid (could be either source or dest)
            call = await Call.find_by_uniqueid(session, uniqueid)
            if not call and dest_uniqueid:
                call = await Call.find_by_uniqueid(session, dest_uniqueid)
                
            if call:
                # Store dial status in metadata for hangup event classification
                current_metadata = call.call_metadata or {}
                call.call_metadata = {
                    **current_metadata,
                    "dial_end": {
                        "time": datetime.now().isoformat(),
                        "dial_status": dial_status,
                        "dest_uniqueid": dest_uniqueid
                    },
                    "dial_status": dial_status  # Store for easy access in hangup
                }
                
                # Handle immediate failures (legitimate carrier rejections)
                if dial_status in ['NOANSWER', 'BUSY', 'CONGESTION', 'CHANUNAVAIL']:
                    call.status = 'failed'
                    
                    # Update campaign state for immediate failures
                    campaign_id = call.campaign_id
                    if campaign_id and isinstance(campaign_id, int) and campaign_id in campaign_states:
                        if campaign_states[campaign_id].active_calls > 0:
                            campaign_states[campaign_id].active_calls -= 1
                        campaign_states[campaign_id].failed_calls += 1
                        logger.info(f"Campaign {campaign_id}: Call {call.call_id} marked as FAILED - {dial_status}")
                        await update_campaign_message(campaign_id)
                
                elif dial_status == 'ANSWER':
                    call.status = 'answered'
                    logger.info(f"Call {call.call_id} answered - waiting for hangup to determine final classification")
                
                await session.commit()
                logger.info(f"Updated call {call.call_id} with DialEnd status: {dial_status}")
            else:
                logger.debug(f"No call found for DialEnd event: UniqueID={uniqueid}")
                
    except Exception as e:
        logger.error(f"Error processing DialEnd event: {e}", exc_info=True)

async def dtmf_begin_listener(manager, event):
    """Handle DTMFBegin events from calls using database for tracking."""
    # Log ALL event fields for analysis
    event_dict = dict(event)
    logger.info("=== DTMFBegin Event Fields ===")
    for key, value in event_dict.items():
        logger.info(f"{key}: {value}")
    logger.info("==============================")
    
    digit = event.get('Digit')
    channel = event.get('Channel')
    uniqueid = event.get('Uniqueid')
    direction = event.get('Direction')  # DTMFBegin includes direction
    
    # Try to get information directly from channel variables
    target_from_event = event.get('TARGET') or event.get('target')
    campaign_from_event = event.get('CAMPAIGNID') or event.get('campaignid')
    tracking_id_from_event = event.get('TRACKINGID') or event.get('trackingid')
    
    logger.info(f"DTMFBegin detected - Digit: {digit}, Channel: {channel}, Direction: {direction}, UniqueID: {uniqueid}, Target: {target_from_event}, Campaign: {campaign_from_event}")
    
    try:
        # Initialize with values from event if available
        target_number = target_from_event or 'Unknown'
        campaign_id = campaign_from_event
        caller_id = event.get('CallerIDNum') or 'Unknown Caller'
        agent_id = 7991166259  # Default agent ID
        
        async with get_async_db_session() as session:
            call = None
            
            # Try finding the call using different methods
            if uniqueid:
                # First try by Asterisk Uniqueid which often contains our call_id
                call = await Call.find_by_uniqueid(session, uniqueid)
                if call:
                    logger.info(f"Found call in database by Uniqueid: {uniqueid}")
            
            if not call and tracking_id_from_event:
                # Then try by tracking ID if available in event
                call = await Call.find_by_tracking_id(session, tracking_id_from_event)
                if call:
                    logger.info(f"Found call in database by TrackingID: {tracking_id_from_event}")
            
            if not call and channel:
                # Finally try by channel name
                call = await Call.find_by_channel(session, channel)
                if call:
                    logger.info(f"Found call in database by Channel: {channel}")
            
            if call:
                # Update the call status to indicate DTMF started
                target_number = call.target_number
                campaign_id = call.campaign_id
                
                # Use agent from database if available, otherwise use from event
                if call.agent_telegram_id:
                    agent_id = call.agent_telegram_id
                
                # Update the call status
                call.status = 'dtmf_started'
                call.call_metadata = {
                    **(call.call_metadata or {}),
                    "dtmf_start": {
                        "time": datetime.now().isoformat(),
                        "digit": digit,
                        "direction": direction
                    }
                }
                await session.commit()
                logger.info(f"Updated call {call.call_id} status to dtmf_started")
            else:
                # If we still don't have a target number but have it from the event, use that
                if target_from_event and target_from_event != 'Unknown':
                    target_number = target_from_event
                    logger.info(f"Using target number from event: {target_number}")
                    
                logger.warning(f"Could not find call in database for Channel: {channel} or Uniqueid: {uniqueid}")

        # Get the caller ID (your number that made the call)
        caller_id = event.get('CallerIDNum') or 'Unknown Caller'
        
        logger.info(f"DTMFBegin '{digit}' pressed - Target: {target_number}, CallerID: {caller_id}, Channel: {channel}, Direction: {direction}")
        
        # Add a delay to allow database operations to complete
        logger.info(f"Adding delay before sending DTMFBegin notification to allow database sync")
        await asyncio.sleep(2)  # 2-second delay
        
        # Try to find the call record again after the delay
        if target_number == 'Unknown':
            async with get_async_db_session() as session:
                # Second attempt to find the call
                if uniqueid:
                    call = await Call.find_by_uniqueid(session, uniqueid)
                    if call:
                        logger.info(f"Found call in database by Uniqueid after delay: {uniqueid}")
                        target_number = call.target_number
                        campaign_id = call.campaign_id
                        agent_id = call.agent_telegram_id or agent_id
                
                if not call and tracking_id_from_event:
                    call = await Call.find_by_tracking_id(session, tracking_id_from_event)
                    if call:
                        logger.info(f"Found call in database by TrackingID after delay: {tracking_id_from_event}")
                        target_number = call.target_number
                        campaign_id = call.campaign_id
                        agent_id = call.agent_telegram_id or agent_id
                
                if not call and channel:
                    call = await Call.find_by_channel(session, channel)
                    if call:
                        logger.info(f"Found call in database by Channel after delay: {channel}")
                        target_number = call.target_number
                        campaign_id = call.campaign_id
                        agent_id = call.agent_telegram_id or agent_id
        
        # DTMFBegin only logs - no notifications sent to avoid duplicates
        logger.info(f"DTMFBegin detected for {target_number} - waiting for DTMFEnd with actual digit")
        
    except Exception as e:
        logger.error(f"Error processing DTMFBegin event: {e}", exc_info=True)

async def dtmf_event_listener(manager, event):
    """Handle DTMF events from calls using database for tracking."""
    # Retrieve application instance from global variable
    global application
    application = global_application_instance
    if not application:
        logger.error("Application instance not found globally. Cannot send DTMF notification.")
        return

    # Log ALL event fields for analysis in debug mode
    event_dict = dict(event)
    logger.debug(f"DTMF Event: {event_dict}")
    
    digit = event.get('Digit')
    channel = event.get('Channel')
    uniqueid = event.get('Uniqueid')
    
    # Try to get tracking and target information directly from event variables
    tracking_id = event.get('TrackingID') or event.get('TRACKINGID') or event.get('trackingid')  # Our primary identifier JKD1.x
    target_from_event = event.get('TARGET') or event.get('target')
    campaign_from_event = event.get('CAMPAIGNID') or event.get('campaignid')
    
    # Log the DTMF press for debugging
    logger.info(f"DTMF '{digit}' detected on channel {channel} (UniqueID: {uniqueid}, TrackingID: {tracking_id}, Target: {target_from_event})")
    
    try:
        # Initialize variables with values from event if available
        target_number = target_from_event or 'Unknown'
        campaign_id = campaign_from_event
        caller_id = event.get('CallerIDNum') or 'Unknown Caller'
        agent_id = 7991166259  # Default agent ID
        
        async with get_async_db_session() as session:
            call = None
            
            # Try finding the call using different methods - prioritize uniqueid
            if uniqueid:
                # First try by Asterisk Uniqueid which often contains our call_id
                call = await Call.find_by_uniqueid(session, uniqueid)
                if call:
                    logger.info(f"Found call in database by Uniqueid: {uniqueid}")
            
            if not call and tracking_id:
                # Then check by tracking_id
                call = await Call.find_by_tracking_id(session, tracking_id)
                if call:
                    logger.info(f"Found call in database by TrackingID: {tracking_id}")
            
            if not call and channel:
                # Finally try by channel name
                call = await Call.find_by_channel(session, channel)
                if call:
                    logger.info(f"Found call in database by Channel: {channel}")
            
            if call:
                # Get the call details
                target_number = call.target_number
                campaign_id = call.campaign_id
                
                # Use agent from database if available, otherwise use from event
                if call.agent_telegram_id:
                    agent_id = call.agent_telegram_id
                
                # Update the call record with DTMF information
                call.status = 'dtmf_processed'
                call.dtmf_digits = (call.dtmf_digits or '') + digit if call.dtmf_digits else digit
                
                # Update call_metadata
                current_metadata = call.call_metadata or {}
                dtmf_history = current_metadata.get('dtmf_history', [])
                dtmf_history.append({
                    "time": datetime.now().isoformat(),
                    "digit": digit,
                    "uniqueid": uniqueid,
                    "channel": channel
                })
                
                call.call_metadata = {
                    **current_metadata,
                    "dtmf_history": dtmf_history,
                    "last_dtmf": {
                        "time": datetime.now().isoformat(),
                        "digit": digit
                    }
                }
                
                await session.commit()
                logger.info(f"Updated call {call.call_id} with DTMF digit {digit}, status now dtmf_processed")
            else:
                logger.warning(f"Could not find call in database for Uniqueid: {uniqueid} or Channel: {channel}")
                
                # Optionally create a new record for unknown DTMF events
                if uniqueid:
                    new_call_id = f"dtmf_unknown_{uniqueid}_{int(time.time())}"
                    new_call = Call(
                        call_id=new_call_id,
                        uniqueid=uniqueid,
                        channel=channel,
                        target_number=caller_id,  # Use caller_id as fallback
                        status="unknown_dtmf",
                        dtmf_digits=digit,
                        start_time=datetime.now(),
                        call_metadata={
                            "detected_time": datetime.now().isoformat(),
                            "origin": "external_dtmf",
                            "dtmf_history": [{
                                "time": datetime.now().isoformat(),
                                "digit": digit,
                                "uniqueid": uniqueid,
                                "channel": channel
                            }]
                        }
                    )
                    session.add(new_call)
                    await session.commit()
                    logger.info(f"Created new record for unknown DTMF: {new_call_id}")
        
        # Add a delay to allow database operations to complete
        logger.info(f"Adding delay before sending DTMF notification to allow database sync")
        await asyncio.sleep(2)  # 2-second delay
        
        # Try to find the call record again after the delay
        if target_number == 'Unknown':
            async with get_async_db_session() as session:
                # Second attempt to find the call
                if uniqueid:
                    call = await Call.find_by_uniqueid(session, uniqueid)
                    if call:
                        logger.info(f"Found call in database by Uniqueid after delay: {uniqueid}")
                        target_number = call.target_number
                        campaign_id = call.campaign_id
                        agent_id = call.agent_telegram_id or agent_id
                
                if not call and tracking_id:
                    call = await Call.find_by_tracking_id(session, tracking_id)
                    if call:
                        logger.info(f"Found call in database by TrackingID after delay: {tracking_id}")
                        target_number = call.target_number
                        campaign_id = call.campaign_id
                        agent_id = call.agent_telegram_id or agent_id
                
                if not call and channel:
                    call = await Call.find_by_channel(session, channel)
                    if call:
                        logger.info(f"Found call in database by Channel after delay: {channel}")
                        target_number = call.target_number
                        campaign_id = call.campaign_id
                        agent_id = call.agent_telegram_id or agent_id
        
        # Format the notification with tracking ID and lead data
        display_tracking_id = "Unknown"
        if call and call.tracking_id:
            display_tracking_id = call.tracking_id
            logger.info(f"Using tracking ID from database: {display_tracking_id}")
        elif tracking_id:
            display_tracking_id = tracking_id
            logger.info(f"Using tracking ID from event: {display_tracking_id}")
        
        # Get lead data from call metadata if available
        lead_data = {}
        if call and call.call_metadata and call.call_metadata.get('lead_data'):
            lead_data = call.call_metadata['lead_data']
        
        # Build rich notification with lead information
        campaign_display = f"{campaign_id}" if campaign_id else display_tracking_id
        
        notification = f"🎯 <b>NEW VICTIM RESPONSE</b>\n\n"
        notification += f"<b>#{campaign_display}</b>\n"
        notification += f"<b>Pressed:</b> <code>{digit}</code>\n\n"
        
        # Add lead information if available
        if lead_data and not lead_data.get('phone_only'):
            notification += f"<b>📋 LEAD INFORMATION</b>\n"
            
            if lead_data.get('name'):
                notification += f"<b>👤 Name:</b> {lead_data['name']}\n"
            
            if lead_data.get('email'):
                notification += f"<b>📧 Email:</b> <code>{lead_data['email']}</code>\n"
            
            if lead_data.get('age'):
                notification += f"<b>🎂 Age:</b> {lead_data['age']}\n"
            
            notification += f"<b>📱 Phone:</b> <code>{target_number}</code>\n"
            
            if lead_data.get('address'):
                notification += f"<b>🏠 Address:</b> {lead_data['address']}\n"
        else:
            # Simple format for phone-only leads
            notification += f"<b>📱 Phone:</b> <code>{target_number}</code>\n"
        
        notification += f"\n<b>⏰ Time:</b> {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
        
        # Send to the chat where the campaign was initiated, not user's DM
        target_chat_id = agent_id  # Default fallback
        if campaign_id and campaign_id in campaign_messages:
            target_chat_id = campaign_messages[campaign_id]["chat_id"]
        
        await application.bot.send_message(
            chat_id=target_chat_id,
            text=notification,
            parse_mode='HTML'
        )
        
        logger.info(f"Sent NEW VICTIM RESPONSE notification to agent {agent_id}")
        
        # P1 Campaign Integration: Update campaign state (only once per DTMF)
        if campaign_id and isinstance(campaign_id, int) and campaign_id in campaign_states:
            # Check if we've already counted this DTMF response for this call
            dtmf_already_counted = False
            if call and call.call_metadata:
                dtmf_already_counted = call.call_metadata.get('dtmf_counted', False)
            
            if not dtmf_already_counted:
                # Update campaign statistics
                campaign_states[campaign_id].dtmf_responses += 1
                logger.info(f"Updated campaign {campaign_id} DTMF responses: {campaign_states[campaign_id].dtmf_responses}")
                
                # Mark this DTMF as counted in the call metadata
                if call:
                    async with get_async_db_session() as session:
                        call_to_update = await Call.find_by_call_id(session, call.call_id)
                        if call_to_update:
                            call_to_update.call_metadata = {
                                **(call_to_update.call_metadata or {}),
                                'dtmf_counted': True
                            }
                            await session.commit()
                
                # Update campaign message in real-time
                await update_campaign_message(campaign_id)
            else:
                logger.info(f"DTMF response for call {call.call_id} already counted, skipping increment")
        
    except Exception as e:
        logger.error(f"Error processing DTMF event: {e}", exc_info=True)

# Global set to track bridges we've already processed
_processed_bridges = set()

async def bridge_event_listener(manager, event):
    """Handle Bridge events to display ICM when a call is actually connected.
    
    This is triggered when the target answers the call and both channels are bridged.
    We only show the ICM once per bridge, regardless of how many channels join.
    """
    # Retrieve application instance from global variable
    global application, _processed_bridges
    application = global_application_instance
    if not application:
        logger.debug("Application instance not found globally in bridge_event_listener.")
        return
        
    # Log the bridge event details
    event_dict = dict(event)
    logger.debug(f"=== Bridge Event ===\n{event_dict}")
    
    # Only process BridgeEnter events (when a channel joins a bridge)
    if event.get('Event') != 'BridgeEnter':
        return
        
    bridge_id = event.get('BridgeUniqueid')
    channel = event.get('Channel')
    channel_state = event.get('ChannelState')
    uniqueid = event.get('Uniqueid', event.get('ChannelUniqueid'))
    
    if not bridge_id or not channel or not uniqueid:
        logger.debug(f"Incomplete bridge event data: bridge_id={bridge_id}, channel={channel}, uniqueid={uniqueid}")
        return
    
    # Check if we've already processed this bridge (not just channel)
    # This ensures we only show the ICM once per call, regardless of which channel triggers it
    if bridge_id in _processed_bridges:
        logger.info(f"Already processed bridge {bridge_id}, skipping")
        return
    
    # Add this bridge to our processed set
    _processed_bridges.add(bridge_id)
    
    # Check if this is a channel in Up state (answered call)
    if channel_state != '6' and event.get('ChannelStateDesc') != 'Up':
        logger.debug(f"Channel {channel} not in Up state, ignoring bridge event")
        _processed_bridges.discard(bridge_id)  # Remove from processed set since we're ignoring it
        return
    
    # We're now tracking by bridge_id only, so no need for these checks
        
    logger.info(f"Processing Bridge event: BridgeUniqueid={bridge_id}, Channel={channel}, Uniqueid={uniqueid}")
    
    # Check if this is a channel in Up state (answered call)
    if channel_state != '6' and event.get('ChannelStateDesc') != 'Up':
        logger.debug(f"Channel {channel} not in Up state, ignoring bridge event")
        _processed_bridges.discard(bridge_channel_key)  # Remove from processed set since we're ignoring it
        return
        
    # Find the call in the database
    try:
        async with get_async_db_session() as session:
            # First try to find the call by uniqueid
            call = await Call.find_by_uniqueid(session, uniqueid)
            
            if not call and channel:
                # Try to find by channel name
                call = await Call.find_by_channel(session, channel)
                
            if not call:
                logger.debug(f"No matching call found for Bridge event: Uniqueid={uniqueid}, Channel={channel}")
                return
                
            # We need to handle both manual calls and campaign calls
            # The key is to only show the ICM when the bridge is established
            # This means the target has answered the call
            logger.info(f"Processing Bridge event for call {call.call_id} (Campaign ID: {call.campaign_id})")
            
            # Check if we've already shown the ICM for this call
            if call.call_metadata and call.call_metadata.get('icm_displayed'):
                logger.info(f"ICM already displayed for call {call.call_id}, not showing again")
                return
                
            # Update call metadata to indicate the bridge has been established
            call.call_metadata = {
                **(call.call_metadata or {}),
                "bridge_time": datetime.now().isoformat(),
                "bridge_id": bridge_id,
                "target_channel": channel,
                "bridge_state": "established"
            }
            
            # Update call status to bridged if not already
            if call.status != "bridged":
                call.status = "bridged"
                
            # Mark that we're going to display the ICM
            call.call_metadata["icm_displayed"] = False
            
            # Save the updated call
            await session.commit()
            
            # Get a fresh copy of the call to avoid session conflicts
            call_id = call.call_id
            
        # In a new session, get the call again and display ICM
        async with get_async_db_session() as new_session:
            # Re-fetch the call to avoid session conflicts
            result = await new_session.execute(select(Call).filter_by(call_id=call_id))
            refreshed_call = result.scalar_one_or_none()
            
            if not refreshed_call:
                logger.error(f"Failed to retrieve call {call_id} in new session")
                return
                
            # Update the existing status message if available, otherwise create a new one
            if refreshed_call.agent_telegram_id:
                try:
                    # Check if we have a stored status message ID
                    status_message_id = refreshed_call.call_metadata.get('status_message_id')
                    status_chat_id = refreshed_call.call_metadata.get('status_chat_id')
                    
                    if status_message_id and status_chat_id:
                        # Update the existing status message
                        logger.info(f"Updating existing status message {status_message_id} for call {refreshed_call.call_id}")
                        
                        # Format the updated status message
                        updated_status = (
                            "📞 *Call Connected*\n\n"
                            f"• *Agent:* `{refreshed_call.agent_phone or 'Unknown'}`\n"
                            f"• *Target:* `{refreshed_call.target_number or 'Unknown'}`\n"
                            f"• *Duration:* 00:00\n"
                            f"• *Status:* Connected\n"
                        )
                        
                        # Create inline keyboard with call control buttons
                        keyboard = [
                            [
                                InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_call_{refreshed_call.call_id}"),
                                InlineKeyboardButton("❌ Hangup", callback_data=f"hangup_call_{refreshed_call.call_id}")
                            ],
                            [
                                InlineKeyboardButton("🔇 Mute", callback_data=f"mute_call_{refreshed_call.call_id}"),
                                InlineKeyboardButton("🔈 Unmute", callback_data=f"unmute_call_{refreshed_call.call_id}")
                            ],
                            [
                                InlineKeyboardButton("⏸ Hold", callback_data=f"hold_call_{refreshed_call.call_id}"),
                                InlineKeyboardButton("▶ Resume", callback_data=f"resume_call_{refreshed_call.call_id}")
                            ],
                            [
                                InlineKeyboardButton("📤 Transfer", callback_data=f"transfer_call_{refreshed_call.call_id}"),
                                InlineKeyboardButton("🔢 DTMF", callback_data=f"dtmf_call_{refreshed_call.call_id}")
                            ]
                        ]
                        
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        # Update the message with new status and buttons
                        await application.bot.edit_message_text(
                            chat_id=status_chat_id,
                            message_id=status_message_id,
                            text=updated_status,
                            reply_markup=reply_markup,
                            parse_mode='Markdown'
                        )
                        
                        # Mark that the ICM has been displayed
                        refreshed_call.call_metadata["icm_displayed"] = True
                        await new_session.commit()
                        
                        logger.info(f"Updated status message with ICM for call {refreshed_call.call_id}")
                    else:
                        # No stored message ID, but ICM not needed for auto-dial campaigns
                        logger.info(f"No status message ID found for call {refreshed_call.call_id}, ICM not needed for auto-dial")
                except Exception as e:
                    logger.error(f"Error updating status message: {str(e)}", exc_info=True)
            else:
                logger.warning(f"Cannot show ICM: Call {call_id} has no agent_telegram_id")
                
    except Exception as e:
        logger.error(f"Error processing bridge event: {str(e)}", exc_info=True)


async def hangup_event_listener(manager, event):
    """Handle Hangup events from calls using AMI event-based classification."""
    # Retrieve application instance from global variable (if needed for future use)
    global application
    application = global_application_instance
    if not application:
        logger.debug("Application instance not found globally in hangup_event_listener.")

    logger.debug(f"Hangup Event: {dict(event)}")
    uniqueid = event.get('Uniqueid')
    channel = event.get('Channel')
    tracking_id = event.get('TrackingID')  # Our primary identifier JKD1.x
    call_id_from_event = event.get('CallID')  # Fallback identifier
    cause = event.get('Cause')
    cause_txt = event.get('Cause-txt')
    hangup_cause = event.get('X-Asterisk-HangupCause')  # Key indicator for blocked calls

    logger.info(f"Hangup Event: Uniqueid={uniqueid}, Channel={channel}, TrackingID={tracking_id}, CallID={call_id_from_event}, Cause={cause}, Cause-txt={cause_txt}, HangupCause={hangup_cause}")

    try:
        if tracking_id or uniqueid or channel or call_id_from_event:
            async with get_async_db_session() as session:
                call = None
                
                # Try finding the call using different methods - CHANGED ORDER to prioritize uniqueid
                # Since we've observed that uniqueid is actually our call_id in many cases
                if uniqueid:
                    # First try by Asterisk Uniqueid which often contains our call_id
                    call = await Call.find_by_uniqueid(session, uniqueid)
                    if not call:
                        # If not found directly, try using uniqueid as call_id (they're often the same)
                        call = await Call.find_by_call_id(session, uniqueid)
                    if call:
                        logger.info(f"Found call in database by Uniqueid/CallID: {uniqueid}")
                
                if not call and call_id_from_event:
                    # Then try by call_id from event variables
                    call = await Call.find_by_call_id(session, call_id_from_event)
                    if call:
                        logger.info(f"Found call in database by CallID: {call_id_from_event}")
                
                if not call and tracking_id:
                    # Then check by tracking_id
                    call = await Call.find_by_tracking_id(session, tracking_id)
                    if call:
                        logger.info(f"Found call in database by TrackingID: {tracking_id}")
                        
                if not call and channel:
                    # Finally try by channel name
                    call = await Call.find_by_channel(session, channel)
                    if call:
                        logger.info(f"Found call in database by Channel: {channel}")
                
                if call:
                    # Update the call status to completed and record end time
                    call.end_time = datetime.now()
                    
                    # Update call_metadata with hangup information
                    call.call_metadata = {
                        **(call.call_metadata or {}),
                        "hangup": {
                            "time": datetime.now().isoformat(),
                            "cause": cause,
                            "cause_txt": cause_txt,
                            "hangup_cause": hangup_cause,
                            "channel": channel
                        }
                    }
                    
                    # AMI EVENT-BASED CLASSIFICATION LOGIC
                    campaign_id = call.campaign_id
                    if campaign_id and isinstance(campaign_id, int) and campaign_id in campaign_states:
                        call_metadata = call.call_metadata or {}
                        dial_status = call_metadata.get('dial_status')
                        had_dtmf = call.status in ['dtmf_processed', 'dtmf_started']
                        
                        # Decrement active calls first
                        if campaign_states[campaign_id].active_calls > 0:
                            campaign_states[campaign_id].active_calls -= 1
                        
                        # Get current retry count for this call
                        current_retry_count = call.call_metadata.get('retry_count', 0) if call.call_metadata else 0
                        call_duration = (call.end_time - call.start_time).total_seconds()
                        
                        # NEW AMI EVENT-BASED CLASSIFICATION WITH RETRY LOGIC
                        if dial_status in ['NOANSWER', 'BUSY', 'CONGESTION', 'CHANUNAVAIL']:
                            # Legitimate carrier rejection - queue for retry
                            classification = "FAILED"
                            call.status = 'failed'
                            campaign_states[campaign_id].failed_calls += 1
                            logger.info(f"Campaign {campaign_id}: Call to {call.target_number} marked as FAILED - {dial_status}")
                            
                            # Queue for retry if under max attempts
                            if current_retry_count < MAX_RETRY_ATTEMPTS:
                                await queue_call_for_retry(campaign_id, call, current_retry_count + 1, "failed", dial_status)
                            else:
                                await send_admin_failure_log(campaign_id, "max_retries", {
                                    "target_number": call.target_number,
                                    "retry_count": current_retry_count
                                })
                            
                        elif dial_status == 'ANSWER' and had_dtmf:
                            # Real human answered and pressed buttons - SUCCESS!
                            campaign_states[campaign_id].completed_calls += 1
                            classification = "COMPLETED"
                            call.status = 'completed'
                            logger.info(f"Campaign {campaign_id}: Call to {call.target_number} marked as COMPLETED - DTMF received")
                            
                        elif dial_status == 'ANSWER' and hangup_cause == "Unallocated (unassigned) number":
                            # Carrier fake response - queue for retry
                            classification = "BLOCKED"
                            call.status = 'blocked'
                            campaign_states[campaign_id].blocked_calls += 1
                            logger.info(f"Campaign {campaign_id}: Call to {call.target_number} marked as BLOCKED - carrier fake response")
                            
                            # Queue for retry if under max attempts
                            if current_retry_count < MAX_RETRY_ATTEMPTS:
                                await queue_call_for_retry(campaign_id, call, current_retry_count + 1, "blocked", "carrier fake response")
                            else:
                                await send_admin_failure_log(campaign_id, "max_retries", {
                                    "target_number": call.target_number,
                                    "retry_count": current_retry_count
                                })
                            
                        elif dial_status == 'ANSWER' and not had_dtmf:
                            # Call answered but no DTMF - queue for retry
                            classification = "FAILED"
                            call.status = 'failed'
                            campaign_states[campaign_id].failed_calls += 1
                            logger.info(f"Campaign {campaign_id}: Call to {call.target_number} marked as FAILED - answered but no DTMF")
                            
                            # Queue for retry if under max attempts
                            if current_retry_count < MAX_RETRY_ATTEMPTS:
                                await queue_call_for_retry(campaign_id, call, current_retry_count + 1, "failed", "no DTMF response")
                            else:
                                await send_admin_failure_log(campaign_id, "max_retries", {
                                    "target_number": call.target_number,
                                    "retry_count": current_retry_count
                                })
                            
                        else:
                            # Fallback case - queue for retry
                            classification = "FAILED"
                            call.status = 'failed'
                            campaign_states[campaign_id].failed_calls += 1
                            logger.info(f"Campaign {campaign_id}: Call to {call.target_number} marked as FAILED - unknown case")
                            
                            # Queue for retry if under max attempts
                            if current_retry_count < MAX_RETRY_ATTEMPTS:
                                await queue_call_for_retry(campaign_id, call, current_retry_count + 1, "failed", f"unknown case (DialStatus: {dial_status})")
                            else:
                                await send_admin_failure_log(campaign_id, "max_retries", {
                                    "target_number": call.target_number,
                                    "retry_count": current_retry_count
                                })
                        
                        logger.info(f"Updated campaign {campaign_id} stats: completed={campaign_states[campaign_id].completed_calls}, active={campaign_states[campaign_id].active_calls}")
                        
                        # Update campaign message in real-time (only shows positive metrics to users)
                        await update_campaign_message(campaign_id)
                        
                        # Send individual notification ONLY for completed calls (hide failures from users)
                        if classification == "COMPLETED":
                            await send_individual_notification(campaign_id, "call_completed", {
                                "target_number": call.target_number,
                                "duration": f"{call_duration:.0f} seconds",
                                "cause": cause_txt or 'Unknown'
                            })
                    
                    await session.commit()
                    logger.info(f"Call {call.call_id} (Uniqueid: {uniqueid}) processed with classification")
                else:
                    logger.debug(f"No call found in database for Uniqueid: {uniqueid}, Channel: {channel}, CallID: {call_id_from_event}")
    except Exception as e:
        logger.error(f"Error processing hangup event: {e}", exc_info=True)

async def originate_autodial_call_from_record(call_id: str, tracking_id: str) -> dict:
    """Originate a call using a pre-created call record."""
    ami_manager = global_application_instance.bot_data.get("ami_manager")
    
    if not ami_manager:
        logger.error("AMI not connected for auto-dial")
        return {'success': False, 'message': 'AMI not connected'}
        
    try:
        # Get the call record from the database
        async with get_async_db_session() as session:
            call = await Call.find_by_call_id(session, call_id)
            if not call:
                logger.error(f"Call record {call_id} not found")
                return {'success': False, 'message': 'Call record not found'}
            
            # Extract needed information from the call record
            target_number = call.target_number
            trunk = call.trunk
            caller_id = call.caller_id
            agent_telegram_id = call.agent_telegram_id
            campaign_id = call.campaign_id
            sequence_number = call.sequence_number
            action_id = f"originate_{call_id}"
            
            # Build the channel name
            channel = f'PJSIP/{target_number}@{trunk}'
            
            # Update the call record
            call.action_id = action_id
            call.channel = channel
            call.status = "initiated"
            await session.commit()
        
        # Build proper AMI action with individual Setvar headers
        logger.info(f"Originating call to {target_number} via {trunk} (Campaign: {campaign_id or 'N/A'})")
        logger.debug(f"Call variables: AgentTelegramID={agent_telegram_id}, CallID={call_id}, TrackingID={tracking_id}, CampaignID={campaign_id}")
            
        # Send originate action with proper variable setting using Variable format
        variables = f"__AgentTelegramID={agent_telegram_id},__CallID={call_id},__TrackingID={tracking_id},__SequenceNumber={sequence_number or 0},__OriginalTargetNumber={target_number},__CallerID={caller_id},__CampaignID={campaign_id or ''},__Origin=autodial,__ActionID={action_id}"
        
        ami_action = {
            'Action': 'Originate',
            'ActionID': action_id,
            'Channel': channel,
            'Context': 'autodial-ivr',
            'Exten': 's',
            'Priority': 1,
            'CallerID': f'"{caller_id}" <{caller_id}>',
            'Async': 'true',
            'Timeout': 45000,  # 45 seconds timeout
            'ChannelId': call_id,  # Use call_id as ChannelId for easier tracking
            'Variable': variables  # Use Variable string format instead of Setvar array
        }
        
        response = await ami_manager.send_action(ami_action)
        
        logger.info(f"Auto-dial originate action sent for {target_number} via {trunk}. Response: {response}")
        
        # Update call status based on response
        async with get_async_db_session() as session:
            call = await Call.find_by_call_id(session, call_id)
            if call:
                if isinstance(response, list): # panoramisk can return a list
                    for event_item in response:
                        if isinstance(event_item, dict) and event_item.get('Response') == 'Error':
                            error_msg = event_item.get('Message', 'Unknown error')
                            logger.error(f"AMI Error for {target_number}: {error_msg}")
                            # Update call status to error
                            call.status = "error"
                            call.call_metadata = {
                                **(call.call_metadata or {}),
                                "error": error_msg,
                                "error_time": datetime.now().isoformat()
                            }
                            await session.commit()
                            return {'success': False, 'message': error_msg}
                elif isinstance(response, dict) and response.get('Response') == 'Error':
                    error_msg = response.get('Message', 'Unknown error')
                    logger.error(f"AMI Error for {target_number}: {error_msg}")
                    # Update call status to error
                    call.status = "error"
                    call.call_metadata = {
                        **(call.call_metadata or {}),
                        "error": error_msg,
                        "error_time": datetime.now().isoformat()
                    }
                    await session.commit()
                    return {'success': False, 'message': error_msg}
                
                # If we made it here, assume success
                call.status = "sending"
                await session.commit()
                return {'success': True, 'message': 'Originate action sent.'}
            else:
                logger.error(f"Call {call_id} not found in database after originate")
                return {'success': False, 'message': 'Database error: Call record not found'}
            
    except Exception as e:
        logger.error(f"Error originating auto-dial call to {target_number}: {str(e)}")
        # Try to mark the call as error in the database if it exists
        try:
            async with get_async_db_session() as session:
                call = await Call.find_by_call_id(session, call_id)
                if call:
                    call.status = "error"
                    call.call_metadata = {
                        **(call.call_metadata or {}),
                        "error": str(e),
                        "error_time": datetime.now().isoformat()
                    }
                    await session.commit()
        except Exception as db_error:
            logger.error(f"Error updating call record after originate error: {db_error}")
        
        return {'success': False, 'message': str(e)}

async def originate_autodial_call(context: ContextTypes.DEFAULT_TYPE, target_number: str, trunk: str, caller_id: str, agent_telegram_id: int, campaign_id: Optional[int] = None, sequence_number: Optional[int] = None) -> dict:
    """Originate a call for an auto-dial campaign through Asterisk AMI using database for tracking."""
    ami_manager = context.application.bot_data.get("ami_manager")
    
    if not ami_manager:
        logger.error("AMI not connected for auto-dial")
        return {'success': False, 'message': 'AMI not connected'}
        
    try:
        # Generate a unique ID for this call with microsecond precision
        timestamp = int(time.time())
        microseconds = datetime.now().microsecond
        # Include sequence number and microseconds to ensure uniqueness
        call_id = f"campaign_{campaign_id or 'none'}_{timestamp}_{sequence_number}_{microseconds}"
        action_id = f"originate_{call_id}"
        
        # Create tracking ID in format JKD1.{sequence_number}
        tracking_id = f"JKD1.{sequence_number}" if sequence_number is not None else f"JKD1.{timestamp % 1000}"
        
        # Build the channel name that will be used
        channel = f'PJSIP/{target_number}@{trunk}'
        
        # Create call record in database
        async with get_async_db_session() as session:
            # Create new call record
            new_call = Call(
                call_id=call_id,
                campaign_id=campaign_id,
                sequence_number=sequence_number,
                tracking_id=tracking_id,  # Our new tracking ID (e.g., JKD1.1)
                agent_telegram_id=agent_telegram_id,
                target_number=target_number,
                caller_id=caller_id,
                trunk=trunk,
                channel=channel,  # Initial channel name
                action_id=action_id,
                status="initiated",
                start_time=datetime.now(),
                # Store additional metadata as JSON
                call_metadata={
                    "timestamp": timestamp,
                    "origin": "autodial",
                    "tracking_id": tracking_id  # Store in metadata too for redundancy
                }
            )
            session.add(new_call)
            await session.commit()
            logger.info(f"Created call record in database: {call_id}")
        
        # Build proper AMI action with individual Setvar headers  
        logger.info(f"Originating call to {target_number} via {trunk} (Campaign: {campaign_id or 'N/A'})")
        logger.debug(f"Call variables: AgentTelegramID={agent_telegram_id}, CallID={call_id}, TrackingID={tracking_id}, CampaignID={campaign_id}")
            
        # Send originate action with proper variable setting using Variable format
        # The call goes directly to the target number into the IVR context
        variables = f"__AgentTelegramID={agent_telegram_id},__CallID={call_id},__TrackingID={tracking_id},__SequenceNumber={sequence_number or 0},__OriginalTargetNumber={target_number},__CallerID={caller_id},__CampaignID={campaign_id or ''},__Origin=autodial,__ActionID={action_id}"
        
        ami_action = {
            'Action': 'Originate',
            'ActionID': action_id,
            'Channel': channel,
            'Context': 'autodial-ivr',
            'Exten': 's',
            'Priority': 1,
            'CallerID': f'"{caller_id}" <{caller_id}>',
            'Async': 'true',
            'Timeout': 45000,  # 45 seconds timeout
            'ChannelId': call_id,  # Use call_id as ChannelId for easier tracking
            'Variable': variables  # Use Variable string format instead of Setvar array
        }
        
        response = await ami_manager.send_action(ami_action)
        
        logger.info(f"Auto-dial originate action sent for {target_number} via {trunk}. Response: {response}")
        
        # Update call status based on response
        async with get_async_db_session() as session:
            call = await Call.find_by_call_id(session, call_id)
            if call:
                if isinstance(response, list): # panoramisk can return a list
                    for event_item in response:
                        if isinstance(event_item, dict) and event_item.get('Response') == 'Error':
                            error_msg = event_item.get('Message', 'Unknown error')
                            logger.error(f"AMI Error for {target_number}: {error_msg}")
                            # Update call status to error
                            call.status = "error"
                            call.call_metadata = {
                                **(call.call_metadata or {}),
                                "error": error_msg,
                                "error_time": datetime.now().isoformat()
                            }
                            await session.commit()
                            return {'success': False, 'message': error_msg}
                elif isinstance(response, dict) and response.get('Response') == 'Error':
                    error_msg = response.get('Message', 'Unknown error')
                    logger.error(f"AMI Error for {target_number}: {error_msg}")
                    # Update call status to error
                    call.status = "error"
                    call.call_metadata = {
                        **(call.call_metadata or {}),
                        "error": error_msg,
                        "error_time": datetime.now().isoformat()
                    }
                    await session.commit()
                    return {'success': False, 'message': error_msg}
                
                # If we made it here, assume success
                call.status = "sending"
                await session.commit()
                return {'success': True, 'message': 'Originate action sent.'}
            else:
                logger.error(f"Call {call_id} not found in database after creation")
                return {'success': False, 'message': 'Database error: Call record not found'}
            
    except Exception as e:
        logger.error(f"Error originating auto-dial call to {target_number}: {str(e)}")
        # Try to mark the call as error in the database if it exists
        try:
            async with get_async_db_session() as session:
                call = await Call.find_by_call_id(session, call_id)
                if call:
                    call.status = "error"
                    call.call_metadata = {
                        **(call.call_metadata or {}),
                        "error": str(e),
                        "error_time": datetime.now().isoformat()
                    }
                    await session.commit()
        except Exception as db_error:
            logger.error(f"Failed to update call error status in database: {db_error}")
            
        return {'success': False, 'message': str(e)}

async def handle_autodial_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Handles the /autodial command, prompting for file upload.
       Returns AUTO_DIAL state if authorized, otherwise MAIN_MENU or END.
    """
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async Query
        agent = result.scalar_one_or_none()

        if not agent:
            if update.message: # Check if update.message exists
                await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return ConversationHandler.END
            
        # Check authorization AFTER finding agent
        if not agent.is_authorized:
            if update.message:
                await update.message.reply_text("❌ You are not authorized to use this bot. Please contact an administrator.")
            return ConversationHandler.END

        # Check if route is configured
        if not agent.route:
            if update.message:
                await update.message.reply_text(
                    "❌ No route configured. Please set your route first:\n\n"
                    "`/route one` or `/route two`\n\n"
                    "Route selection determines which trunk will be used for campaigns."
                )
            try:
                await show_main_menu(update, context, agent)
                return MAIN_MENU
            except Exception as e:
                logger.error(f"Error showing main menu after route check in /autodial: {e}")
                return ConversationHandler.END

    # If authorized, proceed to prompt for file
    if update.message: # Check if update.message exists
        await update.message.reply_text(
            "🤖 *Auto-Dial Setup*\n\n" # Re-add the prompt text
            "Please upload your .txt file containing phone numbers.\n\n"
            "File format requirements:\n"
            "• One phone number per line\n"
            "• E.164 format (e.g., +1234567890)\n"
            "• No empty lines or special characters (other than +)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel & Back to Main Menu", callback_data="back_main")]]), # Add a cancel button
            parse_mode='Markdown'
        )
    return AUTO_DIAL # Enter the AUTO_DIAL state to wait for the file

async def handle_auto_dial_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the uploaded .txt file for auto-dialing."""
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    agent = None # Initialize agent
    # Get agent data
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async Query
        agent = result.scalar_one_or_none()

        if not agent:
            if update.message: 
                await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return ConversationHandler.END
            
        # Check authorization AFTER finding agent
        if not agent.is_authorized:
            if update.message:
                await update.message.reply_text("❌ You are not authorized to use this bot. Please contact an administrator.")
            return ConversationHandler.END

        # Check if route is configured
        if not agent.route:
            if update.message: 
                await update.message.reply_text(
                    "❌ No route configured. Please set your route first:\n\n"
                                        "`/route one` or `/route two`"
                )
            try:
                await show_main_menu(update, context, agent)
                return MAIN_MENU
            except Exception as e:
                logger.error(f"Error showing main menu after route check in file handler: {e}")
                return ConversationHandler.END

    # If authorized, continue with file processing (no more DB interactions in this part)
    if not update.message or not update.message.document:
        if update.message: await update.message.reply_text("Please upload a document.")
        return AUTO_DIAL

    document = update.message.document
    if document.mime_type != 'text/plain' or not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Invalid file type. Please upload a .txt file.")
        return AUTO_DIAL

    try:
        file = await context.bot.get_file(document.file_id)
        # Limit download size to prevent abuse (e.g., 1MB)
        if file.file_size > 1 * 1024 * 1024:
            await update.message.reply_text("❌ File is too large (max 1MB). Please upload a smaller file.")
            return AUTO_DIAL
             
        file_content_bytes = await file.download_as_bytearray()
        file_content = file_content_bytes.decode('utf-8')
        
        lines = file_content.splitlines()
        valid_leads = []
        invalid_lines = []
        processed_count = 0
        line_limit = 10000 # Limit number of lines to process

        for line_num, line in enumerate(lines, 1):
            if line_num > line_limit:
                await update.message.reply_text(f"⚠️ Warning: Processing stopped after {line_limit} lines to prevent abuse.")
                break 
                
            original_line = line.strip()
            if not original_line: 
                continue

            processed_count += 1
            
            # Parse lead data format: "email | Age: XX | Name | Phone | Address"
            lead_data = {}
            phone_number = None
            
            if '|' in original_line:
                # Split by pipe and extract data
                parts = [part.strip() for part in original_line.split('|')]
                phone_found = False
                
                for i, part in enumerate(parts):
                    # Extract email
                    if '@' in part and not lead_data.get('email'):
                        lead_data['email'] = part
                    
                    # Extract age
                    elif part.lower().startswith('age:'):
                        try:
                            lead_data['age'] = int(part.split(':')[1].strip())
                        except:
                            pass
                    
                    # Extract phone number (look for digits)
                    elif re.search(r'\d{7,}', part) and not phone_found:
                        # Extract just the digits and normalize
                        digits = re.sub(r'[^0-9]', '', part)
                        if len(digits) >= 10:
                            if len(digits) == 10:
                                phone_number = '+1' + digits
                            elif len(digits) == 11 and digits.startswith('1'):
                                phone_number = '+' + digits
                            elif len(digits) > 11:
                                # Take the last 10 digits and add +1
                                phone_number = '+1' + digits[-10:]
                            phone_found = True
                    
                    # Extract name (text without digits, email, or age)
                    elif not lead_data.get('name') and not any(char.isdigit() for char in part) and '@' not in part and not part.lower().startswith('age:'):
                        lead_data['name'] = part
                    
                    # Extract address (anything after phone number that contains digits/address-like content)
                    elif phone_found and not lead_data.get('address') and (any(char.isdigit() for char in part) or any(word in part.lower() for word in ['st', 'ave', 'rd', 'dr', 'blvd', 'lane', 'way', 'court', 'place'])):
                        lead_data['address'] = part
            else:
                # Simple phone number format
                normalized = re.sub(r'[^0-9+]', '', original_line)
                if not normalized.startswith('+'):
                    if len(normalized) == 11 and normalized.startswith('1'):
                        phone_number = '+' + normalized
                    elif len(normalized) == 10:
                        phone_number = '+1' + normalized
                    else:
                        phone_number = normalized
                else:
                    phone_number = normalized
                
                # Simple lead data for phone-only format
                lead_data = {'phone_only': True}
            
            # Validate the extracted phone number
            if phone_number and validate_phone_number(phone_number):
                lead_data['phone'] = phone_number
                valid_leads.append(lead_data)
            else:
                invalid_lines.append((line_num, original_line))

        if not valid_leads:
            await update.message.reply_text(
                f"❌ Processed {processed_count} lines, but found no valid phone numbers."
                " Please check the file format and try again."
            )
            return AUTO_DIAL
        
        # Pre-create call records for each valid number
        # This will make parallel dialing more reliable by having records ready before calls are made
        campaign_id = None
        pre_created_calls = []
        
        try:
            # Create a new campaign entry in the database
            campaign_name = context.user_data.get('autodial_campaign_name', f"Campaign {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            
            async with get_async_db_session() as session:
                # Create new campaign
                new_campaign = AutodialCampaign(name=campaign_name, telegram_user_id=user.id)
                session.add(new_campaign)
                await session.flush()  # Get ID before commit
                campaign_id = new_campaign.id
                await session.commit()
                logger.info(f"Created new autodial campaign with ID {campaign_id} for user {user.id}")
                
                # Get agent info for caller ID and trunk
                result = await session.execute(select(Agent).filter_by(telegram_id=user.id))
                agent = result.scalar_one_or_none()
                caller_id = agent.autodial_caller_id if agent and agent.autodial_caller_id else None
                # Use route to determine trunk
                trunk = f"autodial-{agent.route}" if agent and agent.route else "autodial-one"
                
                # Create a call record for each lead
                timestamp = int(time.time())
                for idx, lead in enumerate(valid_leads, 1):
                    phone_number = lead['phone']
                    # Generate a unique tracking ID and call ID
                    tracking_id = f"JKD1.{idx}"
                    # Use nanoseconds and campaign_id to ensure uniqueness
                    import uuid
                    unique_suffix = str(uuid.uuid4())[:8]  # 8 char unique suffix
                    call_id = f"campaign_{campaign_id}_{timestamp}_{idx}_{unique_suffix}"
                    
                    # Create the call record with lead data
                    new_call = Call(
                        call_id=call_id,
                        campaign_id=campaign_id,
                        sequence_number=idx,
                        tracking_id=tracking_id,
                        agent_telegram_id=user.id,
                        target_number=phone_number,
                        caller_id=caller_id,
                        trunk=trunk,
                        status="queued",  # New status to indicate pre-created record
                        start_time=datetime.now(),
                        # Store lead data and metadata as JSON
                        call_metadata={
                            "timestamp": timestamp,
                            "origin": "autodial",
                            "tracking_id": tracking_id,
                            "lead_data": lead  # Store all lead information
                        }
                    )
                    session.add(new_call)
                    pre_created_calls.append({
                        "call_id": call_id,
                        "target_number": phone_number,
                        "sequence_number": idx,
                        "tracking_id": tracking_id,
                        "lead_data": lead
                    })
                
                # Commit all call records at once
                await session.commit()
                logger.info(f"Pre-created {len(pre_created_calls)} call records for campaign {campaign_id}")
        
        except Exception as e:
            logger.error(f"Error pre-creating call records: {e}")
            await update.message.reply_text(
                "⚠️ There was an error preparing the campaign. Please try again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]])
            )
            return AUTO_DIAL
        
        # Store the pre-created calls and campaign ID in user_data
        context.user_data['autodial_leads'] = valid_leads
        context.user_data['autodial_campaign_id'] = campaign_id
        context.user_data['autodial_pre_created_calls'] = pre_created_calls

        response_message = f"✅ Successfully processed file '{document.file_name}'.\n\n"
        response_message += f"• Found {len(valid_leads)} valid leads (out of {processed_count} non-empty lines processed).\n"
        if invalid_lines:
            response_message += f"• Found {len(invalid_lines)} invalid/unparseable lines:\n"
            for line_num, line_content in invalid_lines[:5]: 
                 response_message += f"  - Line {line_num}: '{line_content[:50]}{'...' if len(line_content) > 50 else ''}'\n"
            if len(invalid_lines) > 5:
                 response_message += "  - ... and more\n"
        
        response_message += "\nReady to start the auto-dial campaign?"
        
        keyboard = [
             [InlineKeyboardButton("🚀 Start Dialing", callback_data="start_autodial_campaign")],
             [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")] # This button should be handled by handle_auto_dial
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(response_message, reply_markup=reply_markup)
        return AUTO_DIAL # Stay in AUTO_DIAL state for button press

    except Exception as e:
        logger.error(f"Error processing auto-dial file: {str(e)}")
        await update.message.reply_text("❌ An error occurred while processing the file. Please try again.")
        return AUTO_DIAL

async def handle_auto_dial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle interactions within the Auto-Dial state (buttons only)."""
    query = update.callback_query
    user_id = update.effective_user.id
    agent = None # Initialize agent

    if not query:
        logger.warning("handle_auto_dial called without callback query.")
        return AUTO_DIAL 
        
    await query.answer()
    
    if query.data == "back_main":
        async with get_db_session() as session: # <-- Async context
            # agent = session.query(Agent).filter_by(telegram_id=user_id).first()
            result = await session.execute(select(Agent).filter_by(telegram_id=user_id)) # <-- Async query
            agent = result.scalar_one_or_none()
            if agent: 
                await show_main_menu(update, context, agent) # <-- Await helper
            else:
                # Handle agent not found
                if query.message:
                     await query.message.edit_text("Error: Agent data not found.")
                 # Decide where to go if agent is gone - END might be safest
                return ConversationHandler.END # End conversation if agent gone
        return MAIN_MENU
            
    elif query.data == "start_autodial_campaign":
        # Get the campaign ID from pre-created records
        campaign_id = context.user_data.get('autodial_campaign_id')
        pre_created_calls = context.user_data.get('autodial_pre_created_calls', [])
        
        if not campaign_id or not pre_created_calls:
            logger.error("No pre-created campaign or call records found")
            await query.message.edit_text(
                "⚠️ Campaign preparation data not found. Please upload the file again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]])
            )
            return MAIN_MENU
        
        # P1 Campaign State Initialization
        total_calls = len(pre_created_calls)
        campaign_states[campaign_id] = CampaignState(
            campaign_id=campaign_id,
            user_id=user_id,
            total_calls=total_calls
        )
        
        # Create the campaign monitoring message
        campaign_message = await query.message.edit_text(
            f"🤖 **P1 Campaign #{campaign_id}**\n\n"
            f"📊 **Initializing...**\n"
            f"Total calls: {total_calls}\n\n"
            f"⏱ **Status:** 🚀 Starting",
            parse_mode='Markdown'
        )
        
        # Store message info for future updates - use the actual chat_id where campaign was started
        campaign_messages[campaign_id] = {
            "chat_id": update.effective_chat.id,
            "message_id": campaign_message.message_id
        }
        
        logger.info(f"Initialized P1 campaign {campaign_id} with {total_calls} calls")
        
        # Initialize counters
        successful_originations = 0
        failed_originations = 0
        
        # Set maximum concurrent calls to avoid overloading Asterisk
        MAX_CONCURRENT_CALLS = 5  # Adjust based on your server capacity
        
        # Process pre-created call records in batches
        pre_created_calls = context.user_data.get('autodial_pre_created_calls', [])
        total_calls = len(pre_created_calls)
        processed = 0
        
        # Define a helper function to process a single pre-created call
        async def process_pre_created_call(call_data):
            nonlocal successful_originations, failed_originations
            
            call_id = call_data['call_id']
            target_number = call_data['target_number']
            tracking_id = call_data['tracking_id']
            sequence_number = call_data['sequence_number']
            
            logger.info(f"Dialing pre-created call {call_id} to {target_number} (sequence {sequence_number})")
            
            try:
                # Get the trunk and other info needed for calls
                async with get_async_db_session() as session: 
                    # Look up the pre-created call record
                    call = await Call.find_by_call_id(session, call_id)
                    if not call:
                        logger.error(f"Pre-created call record {call_id} not found")
                        failed_originations += 1
                        return False
                    
                    # Update the call status to 'initiating'
                    call.status = "initiating"
                    await session.commit()
                
                # Use the asterisk_trunk_name from the agent record
                result = await originate_autodial_call_from_record(
                    call_id=call_id,
                    tracking_id=tracking_id
                )
                
                # Check result and update campaign state
                if result.get('success', False):
                    successful_originations += 1
                    # Update campaign state - mark as active when successfully initiated
                    if campaign_id in campaign_states:
                        campaign_states[campaign_id].active_calls += 1
                    logger.info(f"Successfully initiated call to {target_number} (sequence {sequence_number}) - marked as ACTIVE")
                    return True
                else:
                    failed_originations += 1
                    # Update campaign state - mark as failed when initiation fails
                    if campaign_id in campaign_states:
                        campaign_states[campaign_id].failed_calls += 1
                    logger.error(f"Failed to initiate call to {target_number}: {result.get('message', 'Unknown error')} - marked as FAILED")
                    return False
                    
            except Exception as e:
                failed_originations += 1
                logger.error(f"Error dialing {target_number}: {e}")
                return False
        
        # Process calls in batches
        while processed < total_calls:
            # Get the next batch of calls to process
            batch_end = min(processed + MAX_CONCURRENT_CALLS, total_calls)
            current_batch = pre_created_calls[processed:batch_end]
            
            # Process the batch in parallel
            tasks = [process_pre_created_call(call_data) for call_data in current_batch]
            await asyncio.gather(*tasks)
            
            # Update progress
            processed = batch_end
            
            # Update campaign message after each batch
            if campaign_id in campaign_states:
                await update_campaign_message(campaign_id)
            
            # Small delay between batches
            if processed < total_calls:
                await asyncio.sleep(0.5)  # Half-second delay between batches
        
        # Clear the setup data from user_data
        context.user_data.pop('autodial_leads', None)
        context.user_data.pop('autodial_pre_created_calls', None)
        context.user_data.pop('autodial_campaign_id', None)
        
        # Final campaign message update with startup summary
        if campaign_id in campaign_states:
            campaign_states[campaign_id].last_update = datetime.now()
            await update_campaign_message(campaign_id)
        
        logger.info(f"P1 Campaign {campaign_id} launched: {successful_originations} successful, {failed_originations} failed initiations")
        
        # Campaign is now self-managing via real-time message updates
        # User can control it via the campaign message buttons
        return MAIN_MENU
            
    logger.warning(f"Unhandled callback data in AUTO_DIAL state: {query.data}")
    return AUTO_DIAL

# In-memory storage for tracking active calls and campaigns
active_campaigns = {}  # campaign_id: [target_numbers]
active_calls = {}      # call_id: {"campaign_id": x, "target_number": y, ...}

# Interactive Call Menu (ICM) removed - auto-dial only bot does not need call control


def update_call_status(call_id, status, end_time=None):
    """Update the status of a call in our in-memory tracking."""
    if call_id in active_calls:
        active_calls[call_id]['status'] = status
        if end_time:
            active_calls[call_id]['end_time'] = end_time.isoformat()
        logger.debug(f"Updated call {call_id} status to {status}")
    else:
        logger.warning(f"Call ID {call_id} not found in active_calls")

def main():
    """Start the bot using a properly set up event loop."""
    # Set up an event loop for the entire application
    try:
        # Create a new event loop and set it as the current one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run database initialization in this loop
        loop.run_until_complete(init_db())
        logger.info("Database initialized successfully")
        
        # Create the Application with the current event loop
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Add error handler
        application.add_error_handler(error_handler)
        
        # Register post_init callback
        application.post_init = post_init
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        return
    
    # --- Define Handlers to be separated ---


    setautodialcid_handler = CommandHandler("setautodialcid", set_autodial_caller_id)
    route_handler = CommandHandler("route", set_route)

    status_handler = CommandHandler("status", status)

    # Create a filter for authorized users only
    async def authorized_user_filter(update, context):
        """Filter to only allow authorized users."""
        if not update.effective_user:
            return False
        return await is_user_authorized(update.effective_user.id)

    # Add conversation handler for menu navigation and multi-step processes
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("autodial", handle_autodial_command) # /autodial goes to the command handler first
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(handle_main_menu),
                # MessageHandler for document upload should only be in AUTO_DIAL state
            ],
            SETTINGS: [CallbackQueryHandler(handle_settings)],
            PHONE_SETTINGS: [CallbackQueryHandler(handle_phone_settings)],
            CALL_MENU: [CallbackQueryHandler(handle_call_menu)],
            AGENT_MANAGEMENT: [CallbackQueryHandler(handle_agent_management)],
            AGENT_ID_INPUT: [
                CallbackQueryHandler(handle_agent_id_input),  # Handle cancel/back buttons
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(SUPER_ADMIN_ID), handle_agent_id_input)  # Handle agent ID input
            ],
            AUTO_DIAL: [
                CallbackQueryHandler(handle_auto_dial), # Handles button presses like 'start_autodial_campaign' and 'back_main'
                MessageHandler(filters.Document.TEXT, handle_auto_dial_file) # Handles the file upload
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("route", set_route),
        ],
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    # --- Add separated command handlers directly (for use outside conversation) ---


    application.add_handler(setautodialcid_handler)
    application.add_handler(route_handler)

    application.add_handler(status_handler)
    
    # Run the bot
    # application.run_polling() is synchronous, but it runs the async handlers correctly.
    # The Application object manages the event loop needed for the async handlers and post_init.
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # Call main() directly since it's now a synchronous function
    main()