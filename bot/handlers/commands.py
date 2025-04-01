import logging
from typing import Dict
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)
from ..utils.validators import validate_phone_number
from ..database import get_db_session
from ..models import Agent
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - initialize user in database."""
    try:
        user = update.effective_user
        
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=user.id).first()
            if not agent:
                agent = Agent(
                    telegram_id=user.id,
                    username=user.username,
                    is_authorized=user.id == int(context.bot_data.get("SUPER_ADMIN_ID", 0))
                )
                session.add(agent)
                session.commit()
            
            await update.message.reply_text(
                "🎯 *Welcome to Siren Call Center* 🎯\n\n"
                "Your professional call management solution\n"
                "─────────────────────\n\n"
                f"*Status:* {'✅ Authorized' if agent.is_authorized else '❌ Unauthorized'}\n"
                f"*Phone:* 📱 {agent.phone_number or 'Not set'}\n"
                f"*Route:* 🌐 {agent.route or 'Not set'}\n\n"
                "*Available Commands:*\n"
                "📞 /call - Make an outbound call\n"
                "📱 /setphone - Register your phone number\n"
                "🌐 /route - Set your route (M/R/B)\n"
                "ℹ️ /help - Show detailed help",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error in start command: {str(e)}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def call_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Make an outbound call."""
    if not context.args:
        await update.message.reply_text(
            "📞 *Make a Call*\n\n"
            "Please provide the number to call:\n"
            "`/call +1234567890`\n\n"
            "• Must be in E.164 format\n"
            "• Include country code\n"
            "• No spaces or special characters",
            parse_mode='Markdown'
        )
        return

    target_number = context.args[0]
    
    if not validate_phone_number(target_number):
        await update.message.reply_text(
            "❌ Invalid phone number format.\n\n"
            "Please use E.164 format:\n"
            "Example: `/call +1234567890`",
            parse_mode='Markdown'
        )
        return

    with get_db_session() as session:
        try:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            
            if not agent:
                await update.message.reply_text("❌ Error: Agent not found in database.")
                return
            
            if not agent.is_authorized:
                await update.message.reply_text("❌ Error: You are not authorized to make calls.")
                return
                
            if not agent.phone_number:
                await update.message.reply_text(
                    "❌ Error: Please set your phone number first using /setphone"
                )
                return
                
            if not agent.route:
                await update.message.reply_text(
                    "❌ Error: Please select a route first using /route"
                )
                return
            
            # Get call manager
            call_manager = context.bot_data.get("call_manager")
            if not call_manager:
                await update.message.reply_text(
                    "❌ Error: Call system is not available. Please try again later."
                )
                return
            
            # Determine trunk based on route
            if agent.route == "M":
                trunk = "main-trunk"
            elif agent.route == "R":
                trunk = "red-trunk"
            else:  # Black route
                trunk = "black-trunk"
            
            # Send initial status
            status_message = await update.message.reply_text(
                "📞 *Initiating Call*\n\n"
                f"• *Agent:* `{agent.phone_number}`\n"
                f"• *Target:* `{target_number}`\n"
                f"• *Route:* {agent.route} Route\n"
                f"• *Status:* Calling your number...\n\n"
                "_Please answer your phone when it rings._",
                parse_mode='Markdown'
            )
            
            # Use caller_id if set, otherwise use agent's phone number
            caller_id = agent.caller_id or agent.phone_number
            
            # Initiate the call using new call manager
            response = await call_manager.initiate_call(
                agent_number=agent.phone_number,
                target_number=target_number,
                trunk=trunk,
                caller_id=caller_id
            )
            
            if not response['success']:
                await status_message.edit_text(
                    "❌ *Call Failed*\n\n"
                    f"Error: {response.get('message', 'Unknown error')}\n\n"
                    "Please try again later.",
                    parse_mode='Markdown'
                )
                return
            
            # Store call ID for status updates
            context.user_data['current_call_id'] = response['call_id']
            
            # Update status for successful initiation
            await status_message.edit_text(
                "📞 *Call Status*\n\n"
                f"• *Agent:* `{agent.phone_number}`\n"
                f"• *Target:* `{target_number}`\n"
                f"• *Route:* {agent.route} Route\n"
                f"• *Status:* Connecting...\n\n"
                "_Step 1: Calling your number_\n"
                "_Step 2: When you answer, we'll connect to the target_\n\n"
                "Please answer your phone when it rings.",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in call command: {str(e)}")
            await update.message.reply_text("❌ Error accessing database. Please try again later.")
        except Exception as e:
            logger.error(f"Error in call command: {str(e)}")
            await update.message.reply_text("❌ An error occurred. Please try again later.")

async def setphone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's phone number."""
    if not context.args:
        await update.message.reply_text(
            "📱 *Set Phone Number*\n\n"
            "Please provide your phone number in E.164 format:\n"
            "`/setphone +1234567890`\n\n"
            "• Must include country code\n"
            "• Only numbers and + symbol allowed",
            parse_mode='Markdown'
        )
        return
    
    phone_number = context.args[0]
    
    if not validate_phone_number(phone_number):
        await update.message.reply_text(
            "❌ Invalid phone number format.\n\n"
            "Please use E.164 format:\n"
            "Example: `/setphone +1234567890`",
            parse_mode='Markdown'
        )
        return

    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
        
        if agent:
            agent.phone_number = phone_number
            session.commit()
            await update.message.reply_text(
                "✅ Phone number updated successfully!\n\n"
                f"📱 New number: `{phone_number}`",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Error: Agent not found in database.")

async def route_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's route."""
    if not context.args:
        await update.message.reply_text(
            "🌐 *Set Route*\n\n"
            "Please specify your route:\n"
            "`/route M`, `/route R`, or `/route B`\n"
            "`/route Main`, `/route Red`, or `/route Black`\n\n"
            "• M/Main = Main Route\n"
            "• R/Red = Red Route\n"
            "• B/Black = Black Route",
            parse_mode='Markdown'
        )
        return

    route_arg = context.args[0].lower()
    
    # Convert input to proper route value
    if route_arg in ['m', 'main']:
        route = 'M'
    elif route_arg in ['r', 'red']:
        route = 'R'
    elif route_arg in ['b', 'black']:
        route = 'B'
    else:
        await update.message.reply_text(
            "❌ Invalid route.\n\n"
            "Please use:\n"
            "• `/route M` or `/route Main` for Main Route\n"
            "• `/route R` or `/route Red` for Red Route\n"
            "• `/route B` or `/route Black` for Black Route",
            parse_mode='Markdown'
        )
        return

    with get_db_session() as session:
        try:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            
            if not agent:
                await update.message.reply_text("❌ Error: Agent not found in database.")
                return
            
            if not agent.is_authorized:
                await update.message.reply_text("❌ Error: You are not authorized to set a route.")
                return
            
            # Update route
            agent.route = route
            session.commit()
            
            route_name = {
                "M": "Main",
                "R": "Red",
                "B": "Black"
            }.get(route)
            
            await update.message.reply_text(
                f"✅ Route updated successfully!\n\n"
                f"🌐 New Route: *{route_name}*",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in set_route: {str(e)}")
            session.rollback()
            await update.message.reply_text("❌ Error updating route. Please try again later.")

def register_command_handlers(application: Application) -> None:
    """Register all command handlers."""
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("call", call_command))
    application.add_handler(CommandHandler("setphone", setphone_command))
    application.add_handler(CommandHandler("route", route_command)) 