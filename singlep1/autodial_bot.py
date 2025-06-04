# Main file for the Standalone Autodial Bot
import asyncio
import logging
import os
import re
import time  # Ensure time is imported
from datetime import datetime
from typing import List, Optional, Dict

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from sqlalchemy import select

from autodial_database import init_db, get_session
from autodial_models import Agent, AutodialCampaign, AutodialCall # Assuming these will be defined

# AMI (Asterisk Manager Interface) imports - using panoramisk
from panoramisk import Manager

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv(dotenv_path='.env') # Load from the .env file

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))
AMI_HOST = os.getenv("AMI_HOST")
AMI_PORT = int(os.getenv("AMI_PORT"))
AMI_USERNAME = os.getenv("AMI_USERNAME")
AMI_SECRET = os.getenv("AMI_SECRET")
DEFAULT_AUTODIAL_TRUNK_CONTEXT = os.getenv("DEFAULT_AUTODIAL_TRUNK_CONTEXT", "autodial-one") # Default if not set
DEFAULT_MAX_CONCURRENT_CALLS = 50  # Default to 50 concurrent calls per agent

# Conversation states
(UPLOAD_FILE, CONFIRM_CAMPAIGN) = range(2)  # Removed CAMPAIGN_ACTIVE state to allow other commands

# Global AMI Manager instance
ami_manager = None
active_campaign_tasks = {}

# Global application instance (changed from bot_instance to match bot.py pattern)
global_application_instance = None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
    async with get_session() as session:
        agent = await session.get(Agent, user.id) # Assuming Agent primary key is telegram_id
        if not agent:
            # For a standalone bot, decide if new users are automatically added/authorized
            # Set default max concurrent calls to 50 for new agents
            # Or if they need to be pre-authorized by a super admin.
            # For now, let's assume only pre-authorized agents can use it.
            if user.id == SUPER_ADMIN_ID:
                agent = Agent(telegram_id=user.id, username=user.username, is_authorized=True, auto_dial_enabled=True)
                session.add(agent)
                await session.commit()
                await update.message.reply_text(f"Welcome Super Admin, {user.first_name}! You are authorized.")
            else:
                await update.message.reply_text("You are not authorized to use this bot.")
                return
        elif not agent.is_authorized or not agent.auto_dial_enabled:
            await update.message.reply_text("You are not authorized for autodialing.")
            return

    # Fetch campaign stats for the dashboard
    async with get_session() as session:
        # Get all campaigns for this agent
        campaigns = await session.execute(
            select(AutodialCampaign)
            .where(AutodialCampaign.agent_telegram_id == user.id)
            .order_by(AutodialCampaign.created_at.desc())
            .limit(5)  # Show last 5 campaigns
        )
        campaigns = campaigns.scalars().all()

        if campaigns:
            stats_text = ""
            for campaign in campaigns[:3]:  # Show only last 3
                status_emoji = {
                    'active': '🟢',
                    'completed': '✅',
                    'paused': '⏸️',
                    'failed': '❌',
                    'pending': '⏳'
                }.get(campaign.status.lower(), '📋')
                stats_text += f"{status_emoji} *{campaign.name}*\n   └─ {campaign.status.upper()}\n"
        else:
            stats_text = "📭 No campaigns yet. Start your first!"

    await update.message.reply_text(
        f"🎯 *SirenP1 AutoDial Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Welcome back, *{user.first_name}*! 👋\n\n"
        f"📊 *Recent Activity*\n"
        f"{stats_text}\n\n"
        f"⚡ *Quick Actions*\n"
        f"┣ 🚀 /newcampaign - Launch campaign\n"
        f"┣ 📈 /stats - Campaign analytics\n"
        f"┣ 📲 /setcid - Configure Caller ID\n"
        f"┣ 🌐 /route - Select trunk route\n"
        f"┣ ⚙️ /settings - Bot settings\n"
        f"┣ 🔍 /responses - View responses\n"
        f"┗ ❓ /help - Command guide\n\n"
        f"💡 *Tip:* Upload a .txt file anytime to start a campaign!",
        parse_mode='Markdown'
    )

async def set_caller_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /setcid command to set the CallerID."""
    message = update.message
    user = update.effective_user  # Get the user object from update
    
    if not context.args:
        await message.reply_text(
            "*SirenP1 - Set Caller ID*\n"
            "Usage: /setcid <number>\n"
            "\nExample: /setcid +1234567890\n"
            "\nNote: Number must be in E.164 format (with + prefix)",
            parse_mode='Markdown'
        )
        return

    caller_id = context.args[0]
    
    # Simple validation - just check it's not empty
    if not caller_id.strip():
        await message.reply_text("Error: Caller ID cannot be empty. Usage: /setcid <number>")
        return

    async with get_session() as session:
        agent = await session.get(Agent, user.id)
        if not agent:
            agent = Agent(
                telegram_id=user.id, 
                username=user.username, 
                max_concurrent_calls_override=DEFAULT_MAX_CONCURRENT_CALLS,
                autodial_trunk="one"  # Default to trunk one
            )
            session.add(agent)
        agent.autodial_caller_id = caller_id.strip()
        await session.commit()

    await message.reply_text(
        f"✅ *Caller ID Updated!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📲 New Caller ID: `{caller_id}`\n"
        f"🎯 Status: Ready for campaigns\n\n"
        f"💡 All your campaigns will now use this number as the caller ID.",
        parse_mode='Markdown'
    )

async def set_route_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /route command to set the trunk/route."""
    message = update.message
    user = update.effective_user
    
    if not context.args or context.args[0].lower() not in ['one', 'two']:
        await message.reply_text("Usage: /route <one|two>")
        return
    
    route = context.args[0].lower()
    
    async with get_session() as session:
        agent = await session.get(Agent, user.id)
        if not agent:
            agent = Agent(
                telegram_id=user.id,
                username=user.username,
                max_concurrent_calls_override=DEFAULT_MAX_CONCURRENT_CALLS
            )
            session.add(agent)
        agent.autodial_trunk = route
        await session.commit()
    
    route_display = {'one': 'Trunk One', 'two': 'Trunk Two'}.get(route, route)
    await message.reply_text(
        f"✅ *Trunk Route Updated!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 Active Route: `{route_display}`\n"
        f"📡 Context: `autodial-{route}`\n\n"
        f"💡 All campaigns will now use this trunk for outbound calls.",
        parse_mode='Markdown'
    )

def format_campaign_status(status):
    status_emojis = {
        'active': '🟢',
        'completed': '✅',
        'paused': '⏸️',
        'failed': '❌',
        'pending': '⏳'
    }
    return f"{status_emojis.get(status.lower(), '📋')} {status.upper()}"

def format_progress(processed, total):
    if total == 0:
        return "0/0 (0.0%)"
    percentage = (processed / total) * 100
    bar_length = 10
    filled = int(bar_length * processed // total)
    bar = '█' * filled + '░' * (bar_length - filled)
    return f"`{bar}` {processed}/{total} ({percentage:.1f}%)"

def format_campaign_status(status):
    """Format campaign status with emoji"""
    status_map = {
        'active': '🟢 ACTIVE',
        'completed': '✅ COMPLETED',
        'paused': '⏸ PAUSED',
        'failed': '❌ FAILED',
        'pending': '⏳ PENDING'
    }
    return status_map.get(status.lower(), f'❓ {status.upper()}')

def format_progress(processed, total):
    """Format progress bar with percentage"""
    if total == 0:
        return "0/0 (0.0%)"
    percentage = (processed / total) * 100
    bar_length = 10
    filled = int(bar_length * processed // total)
    bar = '█' * filled + '░' * (bar_length - filled)
    return f"`{bar}` {processed}/{total} ({percentage:.1f}%)"

async def get_campaign_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display campaign statistics with clean formatting"""
    user = update.effective_user
    
    async with get_session() as session:
        # Get campaigns for this user
        campaigns = await session.execute(
            select(AutodialCampaign)
            .where(AutodialCampaign.agent_telegram_id == user.id)
            .order_by(AutodialCampaign.created_at.desc())
            .limit(10)
        )
        campaigns = campaigns.scalars().all()

        if not campaigns:
            await update.message.reply_text("📭 No campaigns found. Use /newcampaign to start one.")
            return

        # Build the dashboard
        dashboard = [
            "📊 *CAMPAIGN DASHBOARD*",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📈 Showing {len(campaigns)} most recent campaigns",
            ""
        ]

        for campaign in campaigns:
            # Get call stats
            calls = await session.execute(
                select(AutodialCall)
                .where(AutodialCall.campaign_id == campaign.id)
            )
            calls = calls.scalars().all()
            
            # Calculate metrics
            total = len(calls)
            completed = len([c for c in calls if getattr(c, 'status', '') == 'completed'])
            responded = len([c for c in calls if getattr(c, 'dtmf_response', '') == '1'])
            
            # Add campaign header
            status_emoji = '📌'  # Default
            if hasattr(campaign, 'status'):
                status_emoji = {
                    'active': '🟢',
                    'completed': '✅',
                    'paused': '⏸',
                    'failed': '❌',
                    'pending': '⏳'
                }.get(campaign.status.lower(), '📌')
            
            dashboard.extend([
                f"{status_emoji} *{campaign.name}*",
                f"├─ ID: `{campaign.id}`",
                f"├─ Status: {format_campaign_status(getattr(campaign, 'status', 'unknown'))}",
            ])

            # Add progress if available
            if hasattr(campaign, 'total_numbers'):
                processed = getattr(campaign, 'processed_numbers', 0)
                total_nums = getattr(campaign, 'total_numbers', 0)
                dashboard.append(f"├─ Progress: {format_progress(processed, total_nums)}")
            
            # Add call stats if available
            if calls:
                dashboard.extend([
                    f"├─ 📞 Completed: {completed}/{total} ({completed/max(total,1):.0%})",
                    f"├─ ✅ Responses: {responded} ({responded/max(total,1):.0%})"
                ])
            
            # Add created time if available
            if hasattr(campaign, 'created_at'):
                dashboard.append(f"└─ ⏰ Created: {campaign.created_at.strftime('%b %d %H:%M')}")
            
            dashboard.append("")

        # Add pagination note if needed
        if len(campaigns) == 10:
            dashboard.append("*Use /campaigns <page> to see more*")

        # Send the message
        await update.message.reply_text(
            "\n".join(dashboard),
            parse_mode='Markdown'
        )

async def rename_campaign_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rename a campaign."""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /rename <campaign_id> <new_name>\n\n"
            "Example: /rename 42 Summer_Sale\n"
            "Note: Use underscores for spaces in the name"
        )
        return
    
    try:
        campaign_id = int(context.args[0])
        new_name = '_'.join(context.args[1:])  # Join remaining args as the new name
        
        async with get_session() as session:
            # Get the campaign
            campaign = await session.get(AutodialCampaign, campaign_id)
            if not campaign:
                await update.message.reply_text("❌ Campaign not found.")
                return
            
            if campaign.agent_telegram_id != update.effective_user.id:
                await update.message.reply_text("❌ You can only rename your own campaigns.")
                return
            
            old_name = campaign.name
            campaign.name = new_name
            await session.commit()
            
            await update.message.reply_text(
                f"✅ Campaign renamed successfully!\n"
                f"• Old name: {old_name}\n"
                f"• New name: {new_name}\n"
                f"• Campaign ID: {campaign_id}"
            )
            
    except ValueError:
        await update.message.reply_text("❌ Invalid campaign ID. Please provide a valid number.")
    except Exception as e:
        logger.error(f"Error renaming campaign: {e}")
        await update.message.reply_text("❌ An error occurred while renaming the campaign.")

async def view_responses_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display numbers that responded with '1' in a clean format"""
    user = update.effective_user
    
    async with get_session() as session:
        # Get all campaigns for this agent
        campaigns = await session.execute(
            select(AutodialCampaign)
            .where(AutodialCampaign.agent_telegram_id == user.id)
            .order_by(AutodialCampaign.created_at.desc())
        )
        campaigns = {c.id: c for c in campaigns.scalars().all()}

        if not campaigns:
            await update.message.reply_text("📭 No campaigns found. Use /newcampaign to start one.")
            return

        # Get all calls that responded with 1
        calls = await session.execute(
            select(AutodialCall)
            .where(
                AutodialCall.agent_telegram_id == user.id,
                AutodialCall.response_digit == '1'
            )
            .order_by(AutodialCall.updated_at.desc())
            .limit(100)  # Limit to 100 most recent responses
        )
        calls = calls.scalars().all()

        if not calls:
            await update.message.reply_text("❌ No responses with '1' found.")
            return

        # Group responses by campaign
        campaign_responses = {}
        for call in calls:
            campaign_responses.setdefault(call.campaign_id, []).append(call)

        # Build response message
        response = [
            "✅ *RESPONSES*",
            "Numbers that pressed '1' during calls",
            ""
        ]

        for campaign_id, campaign_calls in campaign_responses.items():
            campaign = campaigns.get(campaign_id)
            if not campaign:
                continue
                
            response.extend([
                f"📋 *{campaign.name}*",
                f"ID: `{campaign_id}` • {len(campaign_calls)} responses",
                ""
            ])
            
            # Sort by most recent first
            sorted_calls = sorted(
                campaign_calls,
                key=lambda x: getattr(x, 'updated_at', datetime.now()),
                reverse=True
            )
            
            # Show up to 5 numbers per campaign
            for call in sorted_calls[:5]:
                response.append(f"• `{call.phone_number}` {getattr(call, 'updated_at', datetime.now()).strftime('%b %d %H:%M')}")
            
            if len(sorted_calls) > 5:
                response.append(f"• ...and {len(sorted_calls) - 5} more")
            
            response.append("")

        # Add summary
        response.extend([
            f"📊 Total responses: {len(calls)}",
            f"📂 Campaigns with responses: {len(campaign_responses)}",
            "",
            "*Note*: Showing up to 5 numbers per campaign"
        ])

        # Send the message (automatically handles long messages)
        await update.message.reply_text(
            "\n".join(response),
            parse_mode='Markdown'
        )

async def get_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /settings command to show current configuration."""
    user = update.effective_user
    
    async with get_session() as session:
        agent = await session.get(Agent, user.id)
        if not agent:
            await update.message.reply_text("No settings found. Please set up your CallerID first with /setcid")
            return
            
        settings_text = (
            "⚙️ *SirenP1 Settings*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📱 *Caller Configuration*\n"
            f"┣ 📲 Caller ID: `{agent.autodial_caller_id or '❌ Not set'}`\n"
            f"┣ 🌐 Trunk Route: `{agent.autodial_trunk or 'one'}`\n"
            f"┗ 📊 Max Concurrent: `{agent.max_concurrent_calls_override or DEFAULT_MAX_CONCURRENT_CALLS}` calls\n\n"
            "🔧 *Quick Configuration*\n"
            "┣ 📲 /setcid `<number>` - Set Caller ID\n"
            "┣ 🌐 /route `<one|two>` - Change trunk\n"
            "┗ 🚀 /newcampaign - Launch campaign\n\n"
            f"💡 *Status:* {'✅ Ready' if agent.autodial_caller_id else '⚠️ Set Caller ID first'}"
        )
        
        await update.message.reply_text(settings_text, parse_mode='Markdown')

async def new_campaign_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the process for a new autodial campaign."""
    user_id = update.effective_user.id
    async with get_session() as session:
        agent = await session.get(Agent, user_id)
        if not agent or not agent.is_authorized or not agent.auto_dial_enabled:
            await update.message.reply_text("You are not authorized to start a campaign.")
            return ConversationHandler.END
        
        if not agent.autodial_caller_id:
            await update.message.reply_text(
                "You need to set a Caller ID first. Use /setcid \"Your Name\" <+1234567890>"
            )
            return ConversationHandler.END
    
    await update.message.reply_text(
        "📂 *Upload Campaign Numbers*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Please upload a `.txt` file containing phone numbers.\n\n"
        "📋 *File Requirements:*\n"
        "• One phone number per line\n"
        "• E.164 format (e.g., `+1234567890`)\n"
        "• UTF-8 or ASCII encoding\n"
        "• Max 10,000 numbers\n\n"
        "💡 *Example:*\n"
        "```\n"
        "+14155551234\n"
        "+13105556789\n"
        "+12125550000\n"
        "```\n\n"
        "⏳ Waiting for your file...",
        parse_mode='Markdown'
    )
    return UPLOAD_FILE

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the .txt file upload for the campaign."""
    user_id = update.effective_user.id
    document = update.message.document
    
    logger.info(f"Received document: {document.file_name}, mime_type: {document.mime_type}")
    
    # Accept any document that ends with .txt regardless of mime type
    if not document or not document.file_name.lower().endswith('.txt'):
        await update.message.reply_text("Invalid file. Please upload a .txt file.")
        return UPLOAD_FILE

    try:
        # Get the file and download content
        file = await context.bot.get_file(document.file_id)
        file_content_bytes = await file.download_as_bytearray()
        
        # Try different encodings if UTF-8 fails
        try:
            file_content = file_content_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                file_content = file_content_bytes.decode('latin-1')
            except Exception as e:
                logger.error(f"Error decoding file content: {e}")
                await update.message.reply_text("Error reading file. Please ensure it's a text file with valid encoding.")
                return UPLOAD_FILE
        
        logger.info(f"File content (first 100 chars): {file_content[:100]}")
        
        phone_numbers = []
        invalid_lines = []
        
        # Process each line in the file
        for i, line in enumerate(file_content.splitlines()):
            stripped_line = line.strip()
            # Check for phone numbers with or without + prefix
            if (stripped_line.startswith('+') and stripped_line[1:].isdigit() and 10 <= len(stripped_line) <= 16) or \
               (stripped_line.isdigit() and 10 <= len(stripped_line) <= 15):
                # Ensure + prefix for consistency
                if not stripped_line.startswith('+'):
                    stripped_line = '+' + stripped_line
                phone_numbers.append(stripped_line)
                logger.info(f"Valid number found: {stripped_line}")
            elif stripped_line: # Non-empty line that is not a valid number
                invalid_lines.append(f"L{i+1}: {stripped_line}")
                logger.info(f"Invalid line: {stripped_line}")
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        await update.message.reply_text(f"Error processing file: {str(e)}")
        return UPLOAD_FILE

    if not phone_numbers:
        await update.message.reply_text("No valid phone numbers found in the file. Please check the format (E.164). Example: +1234567890")
        return UPLOAD_FILE

    context.user_data['phone_numbers'] = phone_numbers
    context.user_data['campaign_name'] = f"Campaign_{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    summary_message = f"📊 *Processing Results*\n"
    summary_message += f"┣ ✅ Valid numbers: `{len(phone_numbers)}`"
    if invalid_lines:
        summary_message += f"\n┣ ⚠️ Invalid lines: `{len(invalid_lines)}`"
        if len(invalid_lines) > 0:
            summary_message += f"\n┗ 📋 Examples: {', '.join(invalid_lines[:3])}"
    else:
        summary_message += f"\n┗ 🎯 All numbers valid!"
    
    await update.message.reply_text(
        f"✅ *File Processing Complete*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{summary_message}\n\n"
        f"📌 *Campaign Details:*\n"
        f"• Name: `{context.user_data['campaign_name']}`\n"
        f"• Numbers: {len(phone_numbers)} ready to dial\n"
        f"• Status: Pending\n\n"
        f"🚀 *Ready to launch?*\n"
        f"┣ ✅ /startcampaign - Begin dialing\n"
        f"┗ ❌ /cancelcampaign - Cancel setup\n\n"
        f"⚡ Campaign will use your configured Caller ID and trunk.",
        parse_mode='Markdown'
    )
    return CONFIRM_CAMPAIGN

async def start_campaign_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirms and starts the campaign."""
    user_id = update.effective_user.id
    phone_numbers = context.user_data.get('phone_numbers')
    campaign_name = context.user_data.get('campaign_name')
    
    # Verify CallerID is set before starting campaign
    async with get_session() as session:
        agent = await session.get(Agent, user_id)
        if not agent or not agent.autodial_caller_id:
            await update.message.reply_text(
                "You need to set a Caller ID first. Use /setcid <number>"
            )
            return ConversationHandler.END

    if not phone_numbers or not campaign_name:
        await update.message.reply_text("No campaign data found. Please start with /newcampaign.")
        return ConversationHandler.END

    try:
        # Create a new campaign entry in the database
        campaign_name = context.user_data.get('campaign_name', 
            f"Campaign_{user_id}_{int(time.time())}")
        
        async with get_session() as session:
            # Get or create agent
            agent = await session.get(Agent, user_id)
            if not agent:
                agent = Agent(
                    telegram_id=user_id,
                    username=update.effective_user.username,
                    max_concurrent_calls_override=DEFAULT_MAX_CONCURRENT_CALLS,
                    autodial_trunk="one"  # Default to trunk one
                )
                session.add(agent)
                await session.flush()
            
            # Create new campaign
            new_campaign = AutodialCampaign(
                name=campaign_name,
                agent_telegram_id=user_id,
                status='pending',
                phone_numbers_json=phone_numbers,  # Include the phone numbers JSON
                campaign_config_json={
                    "caller_id": agent.autodial_caller_id or "",
                    "trunk_context": f"autodial-{agent.autodial_trunk or 'one'}",
                    "created_at": datetime.utcnow().isoformat()
                }
            )
            session.add(new_campaign)
            await session.flush()
            campaign_id = new_campaign.id
            
            # Create call records for each number
            timestamp = int(time.time())
            for idx, number in enumerate(phone_numbers, 1):
                # Generate a unique tracking ID and call ID
                tracking_id = f"JKD1.{idx}"
                microseconds = datetime.now().microsecond
                call_id = f"campaign_{campaign_id}_{timestamp}_{idx}_{microseconds}"
                
                # Create the call record
                call_record = AutodialCall(
                    campaign_id=campaign_id,
                    phone_number=number,
                    status='queued',
                    call_id=call_id,
                    tracking_id=tracking_id,
                    sequence_number=idx,
                    agent_telegram_id=user_id,  # Add the agent's telegram ID
                    call_metadata={
                        "timestamp": timestamp,
                        "origin": "autodial",
                        "tracking_id": tracking_id,
                        "trunk": f"autodial-{agent.autodial_trunk or 'one'}",
                        "caller_id": agent.autodial_caller_id or ""
                    }
                )
                session.add(call_record)
            
            await session.commit()
            logger.info(f"Pre-created {len(phone_numbers)} call records for campaign {campaign_id}")
            
    except Exception as e:
        logger.error(f"Error creating campaign: {e}", exc_info=True)
        await update.message.reply_text("❌ Error creating campaign. Please try again.")
        return ConversationHandler.END

    await update.message.reply_text(
        f"🚀 *Campaign Launched!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 *Campaign ID:* `{campaign_id}`\n"
        f"📋 *Name:* {campaign_name}\n"
        f"📞 *Numbers:* {len(phone_numbers)} queued\n"
        f"⏱️ *Status:* Initializing...\n\n"
        f"🔔 *What happens next:*\n"
        f"• Calls will start dialing automatically\n"
        f"• You'll receive notifications for responses\n"
        f"• Use /stats to monitor progress\n"
        f"• Campaign runs in the background\n\n"
        f"💡 *Tip:* You can start another campaign or use other commands while this runs!",
        parse_mode='Markdown'
    )
    logger.info(f"Campaign {campaign_id} for agent {user_id} starting with {len(phone_numbers)} numbers.")
    
    # Start processing the campaign asynchronously
    campaign_task = asyncio.create_task(process_campaign(campaign_id, context.bot))
    active_campaign_tasks[campaign_id] = campaign_task
    
    context.user_data.clear()
    return ConversationHandler.END

async def process_campaign(campaign_id: int, bot=None):
    """Process a campaign by making calls to all numbers.
    
    Args:
        campaign_id: The ID of the campaign to process
        bot: Optional Telegram bot instance for sending notifications
    """
    logger.info(f"Starting to process campaign ID: {campaign_id}")
    
    # Use the global bot instance if none provided
    global global_application_instance
    if bot is None and global_application_instance is not None:
        bot = global_application_instance.bot
    
    # Ensure AMI is connected before proceeding
    if ami_manager is None or not ami_manager.connected:
        logger.warning(f"AMI not connected. Attempting to connect before processing campaign {campaign_id}...")
        ami_connected = await connect_ami()
        if not ami_connected:
            logger.error(f"Failed to connect to AMI. Cannot process campaign {campaign_id}.")
            
            # Update campaign status to failed
            async with get_session() as session:
                campaign = await session.get(AutodialCampaign, campaign_id)
                if campaign:
                    campaign.status = 'failed'
                    campaign.campaign_config_json = {
                        **(campaign.campaign_config_json or {}),
                        "error": "AMI connection failed"
                    }
                    await session.commit()
                    # Notify the agent about the failure if bot is provided
                    if bot and campaign.agent_telegram_id:
                        try:
                            await bot.send_message(
                                campaign.agent_telegram_id, 
                                f"⚠️ Campaign '{campaign.name}' failed: AMI connection error. Please try again later."
                            )
                        except Exception as e:
                            logger.error(f"Failed to send notification: {e}")
            return

    # Fetch campaign and calls
    async with get_session() as session:
        # Get campaign details
        campaign = await session.get(AutodialCampaign, campaign_id)
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found in DB.")
            return
            
        # Get agent details
        agent = await session.get(Agent, campaign.agent_telegram_id)
        if not agent:
            logger.error(f"Agent {campaign.agent_telegram_id} not found for campaign {campaign_id}")
            return
            
        # Get the agent's max concurrent calls setting
        max_concurrent_calls = agent.max_concurrent_calls_override if agent and agent.max_concurrent_calls_override else DEFAULT_MAX_CONCURRENT_CALLS
        
        # Get all calls for this campaign
        calls_query = await session.execute(
            select(AutodialCall).where(AutodialCall.campaign_id == campaign_id)
        )
        calls = calls_query.scalars().all()
        
        # Update campaign status to active and set total numbers
        campaign.status = 'active'
        campaign.total_numbers = len(calls)
        await session.commit()
        
        # Get campaign configuration
        campaign_config = campaign.campaign_config_json or {}
        trunk_context = campaign_config.get("trunk_context") or agent.autodial_trunk or "autodial-one"
        caller_id = agent.autodial_caller_id or campaign_config.get("caller_id") or "Unknown"
        
        # Format the CallerID properly
        caller_id_formatted = f'"{caller_id}" <{caller_id}>'
        
        # Process each call
        for call in calls:
            # Skip calls that are already processed
            if call.status not in ['queued', 'pending']:
                continue
                
            # Generate tracking ID in same format as bot.py: JKD1.{sequence_number}
            tracking_id = f"JKD1.{call.id}"
            
            # Update the call record with the tracking ID
            call.tracking_id = tracking_id
            await session.commit()
            
            # Format the CallerID with proper escaping for Asterisk
            # We'll use just the number as the CallerID name and number
            caller_id_to_use = caller_id
            caller_id_formatted = f'"{caller_id_to_use}" <{caller_id_to_use}>'
            
            # Generate call_id in same format as bot.py
            timestamp = int(time.time())
            microseconds = datetime.now().microsecond
            call_id = f"campaign_{campaign_id}_{timestamp}_{call.id}_{microseconds}"
            action_id = f"originate_{call_id}"
            
            # Build variables string EXACTLY as bot.py does
            variables = (
                f'__AgentTelegramID={campaign.agent_telegram_id},'  # Double underscore ensures persistence
                f'__CallID={call_id},'  # Original call ID
                f'__TrackingID={tracking_id},'  # Our new primary tracking ID (e.g., JKD1.1)
                f'__SequenceNumber={call.id},'  # Position in the campaign
                f'__OriginalTargetNumber={call.phone_number},'  # Will persist in all contexts
                f'__CallerID={caller_id},'  # Will persist in all contexts
                f'__CampaignID={campaign_id},'  # Will persist in all contexts
                f'__Origin=autodial,'  # Will persist in all contexts
                f'__ActionID={action_id}'  # Will persist in all contexts
            )
            
            originate_action = {
                'Action': 'Originate',
                'ActionID': action_id,
                'Channel': f'PJSIP/{call.phone_number}@{trunk_context}',
                'Context': 'autodial-ivr',
                'Exten': 's',
                'Priority': 1,
                'CallerID': caller_id_formatted,
                'Timeout': 30000,
                'Async': 'true',
                'Variable': variables
            }
            
            # Update call status
            call.status = 'dialing'
            call.attempt_count += 1
            call.last_attempt_time = datetime.now()
            await session.commit()
            
            # Originate the call
            logger.info(f"Originating call: {originate_action}")
            try:
                originate_response = await ami_manager.send_action(originate_action)
                logger.info(f"Originate response for call {call.id}: {originate_response}")
                
                # Update call status based on response
                if isinstance(originate_response, list):
                    for resp in originate_response:
                        if resp.get('Response') == 'Error':
                            call.status = 'failed'
                            call.error_message = resp.get('Message', 'Unknown error')
                            await session.commit()
                            break
                elif isinstance(originate_response, dict) and originate_response.get('Response') == 'Error':
                    call.status = 'failed'
                    call.error_message = originate_response.get('Message', 'Unknown error')
                    await session.commit()
                
                # Wait a bit before next call to avoid flooding
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Failed to originate call {call.id}: {e}")
                call.status = 'failed'
                call.error_message = str(e)
        await session.commit()
    
    # Clean up
    if campaign_id in active_campaign_tasks:
        del active_campaign_tasks[campaign_id]
    
    logger.info(f"Finished originating calls for campaign ID: {campaign_id}. Campaign remains active pending call completions.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed help for all commands."""
    help_text = (
        "🔹 *SirenP1 Autodial Bot - Command Reference* 🔹\n\n"
        "📊 *Campaign Management:*\n"
        "┣ /newcampaign - Start a new autodial campaign\n"
        "┣ /campaigns - List all your campaigns\n"
        "┣ /rename <id> <name> - Rename a campaign\n"
        "┣ /responses - View numbers that responded with 1\n"
        "┗ /cancel - Cancel current campaign setup\n\n"
        "⚙️ *Settings:*\n"
        "┣ /setcid <number> - Set your Caller ID (E.164 format)\n"
        "┣ /route <one|two> - Select trunk route\n"
        "┗ /settings - View current configuration\n\n"
        "📋 *Usage Examples:*\n"
        "• Set Caller ID: `/setcid +1234567890`\n"
        "• Select Route: `/route one`\n"
        "• Rename Campaign: `/rename 42 Summer_Sale`\n"
        "• Start Campaign: Upload .txt file with phone numbers"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

# Event handlers for AMI events
async def on_hangup_event(manager, event):
    """Handle Hangup events from Asterisk."""
    try:
        # logger.debug(f"Hangup event: {event}")
        # Get variables from event
        unique_id = event.get('Uniqueid')
        cause_txt = event.get('Cause-txt')
        # This assumes you're passing variables through with originate or setting them in the dialplan
        call_record_id = event.get('CALL_RECORD_ID')
        campaign_id = event.get('CAMPAIGN_ID')
        # Some events might have variables in a different format
        if not call_record_id and 'ChannelVars' in event:
            call_record_id = event.get('ChannelVars', {}).get('CALL_RECORD_ID')
            campaign_id = event.get('ChannelVars', {}).get('CAMPAIGN_ID')
        
        if call_record_id:
            async with get_session() as session:
                call = await session.get(AutodialCall, int(call_record_id))
                if call:
                    call.status = 'completed'  # Or map cause to a more specific status
                    call.uniqueid = unique_id
                    if cause_txt and cause_txt != 'Normal Clearing':
                        call.error_message = cause_txt
                    await session.commit()
                    logger.info(f"Call {call.id} (Num: {call.phone_number}) for campaign {call.campaign_id} hung up. Cause: {cause_txt}")
    except Exception as e:
        logger.error(f"Error handling hangup event: {e}", exc_info=True)

async def on_userevent(manager, event):
    """Handle AutoDialResponse UserEvent."""
    # Only handle our AutoDialResponse events
    if getattr(event, 'name', '') != 'UserEvent' or event.get('UserEvent') != 'AutoDialResponse':
        return
    # Parse AppData string as fallback for custom headers
    raw_appdata = event.get('AppData') or ''
    parsed = {}
    for part in raw_appdata.split('&'):
        if '=' in part:
            key, val = part.split('=', 1)
            parsed[key.strip()] = val.strip()
    agent_id_str = parsed.get('AgentID') or event.get('AgentID')
    caller_id = parsed.get('CallerID') or event.get('CallerID', 'Unknown Caller')
    pressed_one = parsed.get('PressedOne') or event.get('PressedOne')
    campaign_id = parsed.get('CampaignID') or event.get('CampaignID', 'unknown')
    tracking_id = parsed.get('TrackingID') or event.get('TrackingID')
    logger.info(f"Processing AutoDialResponse - AgentID: {agent_id_str}, CallerID: {caller_id}, PressedOne: {pressed_one}, CampaignID: {campaign_id}")
    if pressed_one == 'Yes' and agent_id_str:
        try:
            agent_id_int = int(agent_id_str)
            async with get_session() as session:
                # Look up the campaign
                campaign = None
                if campaign_id != 'unknown':
                    result = await session.execute(
                        select(AutodialCampaign).filter_by(id=int(campaign_id))
                    )
                    campaign = result.scalar_one_or_none()
                # Update the call record with response
                tracking_id = event.get('TrackingID')
                call = None
                if tracking_id:
                    result = await session.execute(
                        select(AutodialCall).filter_by(tracking_id=tracking_id)
                    )
                    call = result.scalar_one_or_none()
                if call:
                    call.response_digit = '1'
                    call.status = 'responded'
                    await session.commit()
                    logger.info(f"Recorded response for call {call.id}")
            # Build notification
            campaign_text = f"Campaign: {campaign.name}" if campaign else ""
            notification_message = (
                f"✅ *New Auto-Dial Response*\n\n"
                f"📱 Phone: `{caller_id}`\n"
                f"🔘 Response: Pressed 1\n"
                f"{campaign_text}"
            )
            # Send notification
            await global_application_instance.bot.send_message(
                chat_id=agent_id_int,
                text=notification_message,
                parse_mode='Markdown'
            )
            logger.info(f"Sent notification to agent {agent_id_int}")
        except ValueError as ve:
            logger.error(f"Value error processing response: {ve}")
        except Exception as e:
            logger.error(f"Failed to process response or send notification: {e}")

async def on_ami_event(manager, event):
    """Generic event handler to log important events."""
    event_name = event.get('Event')
    # Log specific events for debugging
    if event_name in ['OriginateResponse', 'Newchannel', 'Newstate', 'Bridge']:
        logger.debug(f"AMI Event: {event_name} - {event}")

async def on_dtmf_begin(manager, event):
    """Handle DTMFBegin events from calls."""
    # Log ALL event fields for analysis
    event_dict = dict(event)
    logger.info("=== DTMFBegin Event Fields ===")
    for key, value in event_dict.items():
        logger.info(f"{key}: {value}")
    logger.info("==============================")
    
    digit = event.get('Digit')
    channel = event.get('Channel')
    uniqueid = event.get('Uniqueid')
    direction = event.get('Direction')
    
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
        
        async with get_session() as session:
            call = None
            
            # Try finding the call using different methods
            if uniqueid:
                call = await session.get(AutodialCall, int(uniqueid))
                if call:
                    logger.info(f"Found call in database by Uniqueid: {uniqueid}")
            
            if not call and tracking_id_from_event:
                call = await session.get(AutodialCall, tracking_id_from_event)
                if call:
                    logger.info(f"Found call in database by TrackingID: {tracking_id_from_event}")
            
            if not call and channel:
                call = await session.get(AutodialCall, int(channel))
                if call:
                    logger.info(f"Found call in database by Channel: {channel}")
            
            if call:
                # Update the call status to indicate DTMF started
                target_number = call.phone_number
                campaign_id = call.campaign_id
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

                # Format the notification
                campaign_text = f"• Campaign: `{campaign_id}`\n" if campaign_id else ""
                notification = (
                    "🔔 *DTMF PRESS STARTED*\n\n"
                    f"{campaign_text}"
                    f"• Target: `{target_number}`\n"
                    f"• CallerID: `{caller_id}`\n"
                    f"• Direction: `{direction}`\n"
                    f"• Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
                )

                # Delay to allow database commit and event propagation (mirror bot.py)
                await asyncio.sleep(2)
                if global_application_instance:
                    await global_application_instance.bot.send_message(
                        chat_id=agent_id,
                        text=notification,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Sent DTMFBegin notification to agent {agent_id}")
                else:
                    logger.error("Application instance not found globally. Cannot send DTMFBegin notification.")
            else:
                logger.warning(f"Could not find call in database for Channel: {channel} or Uniqueid: {uniqueid}")
                
    except Exception as e:
        logger.error(f"Error processing DTMFBegin event: {e}", exc_info=True)

async def on_dtmf(manager, event):
    """Handle DTMF events from calls."""
    # Log ALL event fields for analysis
    event_dict = dict(event)
    logger.debug(f"DTMF Event: {event_dict}")
    
    digit = event.get('Digit')
    channel = event.get('Channel')
    uniqueid = event.get('Uniqueid')
    
    # Try to get tracking and target information directly from event variables
    tracking_id = event.get('TrackingID') or event.get('TRACKINGID') or event.get('trackingid')
    target_from_event = event.get('TARGET') or event.get('target')
    campaign_from_event = event.get('CAMPAIGNID') or event.get('campaignid')
    
    logger.info(f"DTMF '{digit}' detected on channel {channel} (UniqueID: {uniqueid}, TrackingID: {tracking_id}, Target: {target_from_event})")
    
    try:
        # Initialize variables with values from event if available
        target_number = target_from_event or 'Unknown'
        campaign_id = campaign_from_event
        caller_id = event.get('CallerIDNum') or 'Unknown Caller'
        
        async with get_session() as session:
            call = None
            
            # Try finding the call using different methods
            if uniqueid:
                call = await session.get(AutodialCall, int(uniqueid))
                if call:
                    logger.info(f"Found call in database by Uniqueid: {uniqueid}")
            
            if not call and tracking_id:
                call = await session.get(AutodialCall, int(tracking_id))
                if call:
                    logger.info(f"Found call in database by TrackingID: {tracking_id}")
            
            if not call and channel:
                call = await session.get(AutodialCall, int(channel))
                if call:
                    logger.info(f"Found call in database by Channel: {channel}")
            
            if call:
                # Get the call details
                target_number = call.phone_number
                campaign_id = call.campaign_id
                agent_id = call.agent_telegram_id
                
                # Update the call record with DTMF information
                call.status = 'dtmf_processed'
                call.dtmf_digits = (call.dtmf_digits or '') + digit if call.dtmf_digits else digit
                call.call_metadata = {
                    **(call.call_metadata or {}),
                    "dtmf_end": {
                        "time": datetime.now().isoformat(),
                        "digit": digit
                    }
                }
                await session.commit()
                logger.info(f"Updated call {call.call_id} with DTMF digit {digit}")

                # Format the notification
                campaign_text = f"• Campaign: `{campaign_id}`\n" if campaign_id else ""
                notification = (
                    "🔔 *DTMF DIGIT RECEIVED*\n\n"
                    f"{campaign_text}"
                    f"• Target: `{target_number}`\n"
                    f"• CallerID: `{caller_id}`\n"
                    f"• Digit: `{digit}`\n"
                    f"• Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
                )

                # Delay to allow database commit and event propagation (mirror bot.py)
                await asyncio.sleep(2)
                if global_application_instance:
                    await global_application_instance.bot.send_message(
                        chat_id=agent_id,
                        text=notification,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Sent DTMF notification to agent {agent_id}")
                else:
                    logger.error("Application instance not found globally. Cannot send DTMF notification.")
            else:
                logger.warning(f"Could not find call in database for Channel: {channel} or Uniqueid: {uniqueid}")
                
    except Exception as e:
        logger.error(f"Error processing DTMF event: {e}", exc_info=True)

# Instead of a long-running listener function, we register event callbacks

async def connect_ami(retry_count=3, retry_delay=5):
    """Connects to Asterisk Manager Interface with retry logic."""
    global ami_manager
    
    for attempt in range(retry_count):
        try:
            # Enable verbose logging for panoramisk
            panoramisk_logger = logging.getLogger('panoramisk')
            panoramisk_logger.setLevel(logging.DEBUG)
            
            # Log all environment variables related to AMI for troubleshooting
            logger.info(f"AMI configuration - Host: {AMI_HOST}, Port: {AMI_PORT}, Username: {AMI_USERNAME}")
            
            if not AMI_HOST or not AMI_PORT or not AMI_USERNAME or not AMI_SECRET:
                logger.error("One or more AMI environment variables are missing. Check your .env file.")
                logger.error(f"AMI_HOST: {AMI_HOST}, AMI_PORT: {AMI_PORT}, AMI_USERNAME: {AMI_USERNAME}, AMI_SECRET: {'*' * len(AMI_SECRET) if AMI_SECRET else None}")
                return False
            
            logger.info(f"Connecting to AMI at {AMI_HOST}:{AMI_PORT}... (Attempt {attempt+1}/{retry_count})")
            ami_manager = Manager(
                host=AMI_HOST,
                port=AMI_PORT,
                username=AMI_USERNAME,
                secret=AMI_SECRET,
                encoding='utf8',
                ping_delay=10,  # Send a ping every 10 seconds to keep connection alive
                reconnect=True  # Auto-reconnect if connection is lost
            )
            
            # Register event callbacks
            ami_manager.register_event('Hangup', on_hangup_event)
            ami_manager.register_event('UserEvent', on_userevent)
            ami_manager.register_event('DTMFBegin', on_dtmf_begin)
            ami_manager.register_event('DTMFEnd', on_dtmf)
            
            # Register handlers for AMI connection events - register each event individually
            ami_manager.register_event('OriginateResponse', on_ami_event)
            ami_manager.register_event('Newchannel', on_ami_event)
            ami_manager.register_event('Newstate', on_ami_event)
            ami_manager.register_event('Bridge', on_ami_event)
            
            # Connect to AMI - will raise an exception if it fails
            await ami_manager.connect()
            
            # Test the connection by sending a simple Ping action
            response = await ami_manager.send_action({'Action': 'Ping'})
            if response.get('Response') == 'Success':
                logger.info("Successfully connected to AMI and verified with Ping.")
                return True
            else:
                logger.warning(f"AMI connection seems problematic. Ping response: {response}")
                if attempt < retry_count - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    ami_manager = None
                    return False
                
        except Exception as e:
            logger.error(f"Failed to connect to AMI (Attempt {attempt+1}/{retry_count}): {e}", exc_info=True)
            if attempt < retry_count - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                ami_manager = None
                return False
    
    return ami_manager is not None

async def cancel_campaign_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current campaign setup or an active one (if implemented)."""
    context.user_data.clear()
    await update.message.reply_text("Campaign setup cancelled.")
    # Add logic here to stop an active campaign if needed by campaign_id
    return ConversationHandler.END

async def post_init_tasks(application: Application):
    """Tasks to run after the bot application has been initialized."""
    # Store the bot instance globally for event handlers to use
    global global_application_instance
    global_application_instance = application
    logger.info("Application instance stored globally for event handlers")
    
    # Initialize database schema
    await init_db()
    logger.info("Database initialized successfully")
    
    # Wait a moment to ensure application is fully ready before connecting AMI
    await asyncio.sleep(2)
    
    # Connect to Asterisk AMI with retries (same pattern as bot.py)
    ami_connected = await connect_ami(retry_count=5, retry_delay=3)
    if ami_connected:
        logger.info("AMI connection established successfully during startup.")
        
        # Send a test message to verify notification capability (same as bot.py)
        test_user_id = SUPER_ADMIN_ID  # Use the super admin for testing
        test_message = "🔔 *SirenP1 Test Notification*\n\nThis is a test message to confirm that the autodial bot can send notifications to you."
        try:
            await application.bot.send_message(
                chat_id=test_user_id,
                text=test_message,
                parse_mode='Markdown'
            )
            logger.info(f"Test notification sent to user {test_user_id}")
        except Exception as e:
            logger.error(f"Failed to send test notification to user {test_user_id}: {e}")
    else:
        logger.error("Failed to establish AMI connection during startup. Will retry when needed.")
    
    # Start a background task to ensure AMI stays connected
    asyncio.create_task(maintain_ami_connection())

async def maintain_ami_connection():
    """Background task to ensure AMI connection is maintained."""
    while True:
        if ami_manager is None or not ami_manager.connected:
            logger.warning("AMI connection lost or not established. Attempting to reconnect...")
            await connect_ami()
        await asyncio.sleep(60)  # Check connection every minute

async def async_main():
    """Async main function to set up and run the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not set. Exiting.")
        return

    # Initialize database
    await init_db()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init_tasks).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newcampaign", new_campaign_command)],
        states={
            UPLOAD_FILE: [MessageHandler(filters.Document.ALL, handle_file_upload)],
            CONFIRM_CAMPAIGN: [CommandHandler("startcampaign", start_campaign_command)],
            # Removed CAMPAIGN_ACTIVE state to allow other commands during campaign
        },
        fallbacks=[CommandHandler("cancelcampaign", cancel_campaign_command)],
    )

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", get_campaign_stats))
    application.add_handler(CommandHandler("campaigns", get_campaign_stats))  # Alias for /stats
    application.add_handler(CommandHandler("responses", view_responses_command))
    application.add_handler(CommandHandler("setcid", set_caller_id_command))
    application.add_handler(CommandHandler("route", set_route_command))
    application.add_handler(CommandHandler("settings", get_settings_command))
    application.add_handler(CommandHandler("cancel", cancel_campaign_command))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("newcampaign", new_campaign_command))  

    logger.info("Autodial Bot starting polling...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Run the application until we press Ctrl+C
    try:
        while True:
            await asyncio.sleep(1)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutting down...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

def main() -> None:
    """Main entry point that runs the async main function."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Autodial Bot shutting down...")
    except Exception as e:
        logger.error(f"Autodial Bot crashed: {e}", exc_info=True)

if __name__ == "__main__":
    main()
