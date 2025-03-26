import logging
import os
import re
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
)
from database import init_db, get_session
from models import Agent
from sqlalchemy.exc import SQLAlchemyError
from contextlib import contextmanager
from panoramisk import Manager
import asyncio

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

# Conversation states
(MAIN_MENU, SETTINGS, PHONE_SETTINGS, 
 CALL_MENU, AGENT_MANAGEMENT) = range(5)

def validate_phone_number(number: str) -> bool:
    """Validate phone number in E.164 format."""
    pattern = r'^\+[1-9]\d{1,14}$'
    return bool(re.match(pattern, number))

@contextmanager
def get_db_session():
    session = next(get_session())
    try:
        yield session
    finally:
        session.close()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    error_message = "An error occurred while processing your request. Please try again later."
    
    if update.effective_message:
        await update.effective_message.reply_text(error_message)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: Agent) -> None:
    """Show the main menu."""
    keyboard = [
        [InlineKeyboardButton("📞 Make a Call", callback_data="make_call")],
        [InlineKeyboardButton("📱 My Phone Number", callback_data="phone_number")],
        [InlineKeyboardButton("📊 Call History", callback_data="call_history")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
    ]
    
    if update.effective_user.id == SUPER_ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👥 Manage Agents", callback_data="manage_agents")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Get route display
    route_display = f" (Route: {agent.route})" if agent.route else " (No Route)"
    
    welcome_message = (
        "🎯 *Welcome to Siren Call Center* 🎯\n\n"
        "Your professional call management solution\n"
        "─────────────────────\n\n"
        "*Status:* " + ('✅ Authorized' if agent.is_authorized else '❌ Unauthorized') + "\n"
        "*Phone:* 📱 " + (agent.phone_number or 'Not set') + " _(Registered)_\n"
        "*CallerID:* 📲 " + (agent.caller_id or agent.phone_number or 'Not set') + route_display + "\n\n"
        "*Available Commands:*\n"
        "📞 /call - Make an outbound call\n"
        "📱 /setphone - Register your phone number\n"
        "📲 /setcid - Set outbound caller ID\n"
        "🌐 /route - Set your route (M/R/B)\n"
        "📊 /history - View your call history\n"
        "ℹ️ /help - Show detailed help\n\n"
        "Please select an option from the menu below:"
    )

    if isinstance(update.callback_query, type(None)):
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.message.edit_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start command - shows the main menu."""
    try:
        user = update.effective_user
        
        with get_db_session() as session:
            try:
                agent = session.query(Agent).filter_by(telegram_id=user.id).first()
                if not agent:
                    agent = Agent(
                        telegram_id=user.id,
                        username=user.username,
                        is_authorized=user.id == SUPER_ADMIN_ID
                    )
                    session.add(agent)
                    session.commit()
                
                await show_main_menu(update, context, agent)
                return MAIN_MENU
                
            except SQLAlchemyError as e:
                logger.error(f"Database error: {str(e)}")
                error_msg = "Error accessing database. Please try again later."
                if update.callback_query:
                    await update.callback_query.message.edit_text(error_msg)
                else:
                    await update.message.reply_text(error_msg)
                return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in start command: {str(e)}")
        error_msg = "An error occurred. Please try again later."
        if update.callback_query:
            await update.callback_query.message.edit_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return ConversationHandler.END

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu button presses."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "make_call":
        await query.message.edit_text(
            "📞 *Make a Call*\n\n"
            "To make a call, use the command:\n"
            "`/call <number>`\n\n"
            "Example: `/call +1234567890`\n\n"
            "• Number format: E.164 or US format\n"
            "• International prefix required\n"
            "• No spaces or special characters\n\n"
            "Need help? Use /help for more information.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return CALL_MENU
    
    elif query.data == "phone_number":
        await query.message.edit_text(
            "📱 *Phone Number Settings*\n\n"
            "To set your phone number, use:\n"
            "`/setphone <your_number>`\n\n"
            "Example: `/setphone +1234567890`\n\n"
            "• Use international format\n"
            "• Include country code\n"
            "• No spaces or special characters",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return PHONE_SETTINGS
    
    elif query.data == "call_history":
        await query.message.edit_text(
            "📊 *Call History*\n\n"
            "Your recent calls will appear here.\n"
            "_(Feature coming soon)_\n\n"
            "• View past calls\n"
            "• Call duration\n"
            "• Call status",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return MAIN_MENU
    
    elif query.data == "settings":
        keyboard = [
            [InlineKeyboardButton("🌐 Select Route", callback_data="select_route")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "⚙️ *Settings*\n\n"
            "Select an option below:\n\n"
            "• 🌐 *Route Selection* - Choose your call route\n"
            "• More settings coming soon\n",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SETTINGS
    
    elif query.data == "manage_agents" and update.effective_user.id == SUPER_ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("👥 List All Agents", callback_data="list_agents")],
            [InlineKeyboardButton("✅ Authorize Agent", callback_data="auth_agent")],
            [InlineKeyboardButton("❌ Deauthorize Agent", callback_data="deauth_agent")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "👥 *Agent Management*\n\n"
            "Manage your call center agents:\n\n"
            "• View all agents\n"
            "• Authorize new agents\n"
            "• Manage permissions\n"
            "• View agent statistics\n\n"
            "Select an option:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return AGENT_MANAGEMENT

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle settings menu interactions."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_main":
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            await show_main_menu(update, context, agent)
            return MAIN_MENU
            
    elif query.data == "select_route":
        keyboard = [
            [
                InlineKeyboardButton("🌍 Main Route", callback_data="route_main"),
                InlineKeyboardButton("🔴 Red Route", callback_data="route_red")
            ],
            [InlineKeyboardButton("⚫ Black Route", callback_data="route_black")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "🌐 *Route Selection*\n\n"
            "Please select your preferred route:\n\n"
            "• 🌍 *Main Route* - Primary Route\n"
            "• 🔴 *Red Route* - Secondary Route\n"
            "• ⚫ *Black Route* - Universal Route\n\n"
            "Select your route:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SETTINGS
        
    elif query.data.startswith("route_"):
        route = query.data.split("_")[-1]
        route_name = {
            "main": "Main",
            "red": "Red",
            "black": "Black"
        }.get(route)
        
        if route_name:
            route = "M" if route == "main" else "R" if route == "red" else "B"
        
        # Confirmation keyboard
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Switch Route", callback_data=f"confirm_route_{route}"),
                InlineKeyboardButton("❌ Cancel", callback_data="back_settings")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text(
            f"⚠️ *Confirm Route Change*\n\n"
            f"Are you sure you want to switch to the *{route_name} Route*?\n"
            f"All outbound calls will go through this route.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SETTINGS
        
    elif query.data.startswith("confirm_route_"):
        route = query.data.split("_")[-1]
        route_name = {
            "M": "Main",
            "R": "Red",
            "B": "Black"
        }.get(route)
        
        if route_name:
            route = "M" if route == "main" else "R" if route == "red" else "B"
        
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            if agent:
                agent.route = route
                session.commit()
                
        keyboard = [[InlineKeyboardButton("🔙 Back to Settings", callback_data="back_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text(
            f"✅ *Route Updated Successfully*\n\n"
            f"🌐 New Route: *{route_name}*",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SETTINGS
        
    elif query.data == "back_settings":
        keyboard = [
            [InlineKeyboardButton("🌐 Select Route", callback_data="select_route")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "⚙️ *Settings*\n\n"
            "Select an option below:\n\n"
            "• 🌐 *Route Selection* - Choose your call route\n"
            "• More settings coming soon\n",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SETTINGS
            
    return SETTINGS

async def handle_phone_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle phone settings menu interactions."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_main":
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            await show_main_menu(update, context, agent)
            return MAIN_MENU
            
    return PHONE_SETTINGS

async def handle_call_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle call menu interactions."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_main":
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            await show_main_menu(update, context, agent)
            return MAIN_MENU
            
    return CALL_MENU

async def handle_agent_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle agent management menu interactions."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_main":
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            await show_main_menu(update, context, agent)
            return MAIN_MENU
            
    if update.effective_user.id != SUPER_ADMIN_ID:
        await query.message.edit_text("❌ Unauthorized access.")
        return MAIN_MENU
        
    return AGENT_MANAGEMENT

async def set_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def set_caller_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's outbound caller ID."""
    if not context.args:
        await update.message.reply_text(
            "📲 *Set Outbound CallerID*\n\n"
            "Please provide a phone number in E.164 format:\n"
            "`/setcid +1234567890`\n\n"
            "• Must include country code\n"
            "• Only numbers and + symbol allowed\n"
            "• If not set, uses registered phone",
            parse_mode='Markdown'
        )
        return

    caller_id = context.args[0]
    
    if not validate_phone_number(caller_id):
        await update.message.reply_text(
            "❌ Invalid phone number format.\n\n"
            "Please use E.164 format:\n"
            "Example: `/setcid +1234567890`",
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
                await update.message.reply_text("❌ Error: You are not authorized to set a caller ID.")
                return
            
            # Store old caller_id for history
            old_caller_id = agent.caller_id
            
            # Update caller_id
            agent.caller_id = caller_id
            session.add(agent)
            
            # Add to history
            from models import CallerIDHistory
            history = CallerIDHistory(
                agent_id=agent.id,
                old_caller_id=old_caller_id,
                new_caller_id=caller_id
            )
            session.add(history)
            
            session.commit()
            
            await update.message.reply_text(
                "✅ CallerID updated successfully!\n\n"
                f"📲 New CallerID: `{caller_id}`",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in set_caller_id: {str(e)}")
            session.rollback()
            await update.message.reply_text("❌ Error updating caller ID. Please try again later.")

async def set_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    if update.effective_user.id != SUPER_ADMIN_ID:
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

async def originate_call(context: ContextTypes.DEFAULT_TYPE, agent_number: str, target_number: str, trunk: str, caller_id: str = None) -> dict:
    """Originate a call through Asterisk AMI."""
    ami_manager = context.application.bot_data.get("ami_manager")
    
    if not ami_manager:
        logger.error("AMI not connected")
        return {'success': False, 'message': 'AMI not connected'}
        
    try:
        # Use full E.164 format for all numbers
        agent_dial = agent_number
        target_dial = target_number
        
        # Only use the explicitly set caller_id, never fallback to agent number
        if not caller_id:
            logger.error("No caller ID configured")
            return {'success': False, 'message': 'No caller ID configured'}
        
        # Build variables string for two-stage dialing
        variables = (
            f'AGENT_NUMBER={agent_dial},'
            f'TARGET_NUMBER={target_dial},'
            f'CALLER_ID="{caller_id}" <{caller_id}>'
        )
        
        # Send originate action
        response = await ami_manager.send_action({
            'Action': 'Originate',
            'Channel': f'PJSIP/{agent_dial}@{trunk}',
            'Context': f'from-{trunk}',
            'Exten': 'outbound',
            'Priority': 1,
            'Callerid': f'"{caller_id}" <{caller_id}>',
            'Async': 'true',
            'Variable': variables,
            'Timeout': 30000
        })
        
        # Handle response properly - it's a list of events
        if isinstance(response, list):
            # Check for error in any of the events
            for event in response:
                if isinstance(event, dict):
                    if event.get('Response') == 'Error':
                        logger.error(f"AMI Error: {event.get('Message', 'Unknown error')}")
                        return {'success': False, 'message': event.get('Message', 'Unknown error')}
            # If we got here, assume success
            return {'success': True}
        else:
            # Single response
            logger.info(f"Call originate response: {response}")
            return {'success': True}
            
    except Exception as e:
        logger.error(f"Error originating call: {str(e)}")
        return {'success': False, 'message': str(e)}

async def call(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                    "❌ Error: Please select a route first using /route or the Settings menu."
                )
                return
            
            # Check AMI connection first
            if not await check_ami_status(context):
                await update.message.reply_text(
                    "❌ Error: AMI connection is not available. Please try again later."
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
            
            # Initiate the call
            response = await originate_call(
                context,
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
            
            # Update status for successful initiation
            await status_message.edit_text(
                "📞 *Call Status*\n\n"
                f"• *Agent:* `{agent.phone_number}`\n"
                f"• *Target:* `{target_number}`\n"
                f"• *Route:* {agent.route} Route\n"
                f"• *Status:* Connecting...\n\n"
                "_Step 1: Calling your number_\n"
                "_Step 2: When you answer, we'll call the target_\n\n"
                "Please answer your phone when it rings.",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in call command: {str(e)}")
            await update.message.reply_text("❌ Error accessing database. Please try again later.")
        except Exception as e:
            logger.error(f"Error in call command: {str(e)}")
            await update.message.reply_text("❌ An error occurred. Please try again later.")

async def post_init(application: Application) -> None:
    """Post initialization hook for the bot to set up AMI connection."""
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
        
        # Store in application context for access in handlers
        application.bot_data["ami_manager"] = ami_manager
    except Exception as e:
        logger.error(f"Failed to establish AMI connection: {str(e)}")
        application.bot_data["ami_manager"] = None

def main():
    """Start the bot."""
    # Initialize database
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {str(e)}")
        return

    # Create the Application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(handle_main_menu)],
            SETTINGS: [CallbackQueryHandler(handle_settings)],
            PHONE_SETTINGS: [CallbackQueryHandler(handle_phone_settings)],
            CALL_MENU: [CallbackQueryHandler(handle_call_menu)],
            AGENT_MANAGEMENT: [CallbackQueryHandler(handle_agent_management)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("setphone", set_phone),
            CommandHandler("setcid", set_caller_id),
            CommandHandler("route", set_route),
            CommandHandler("call", call),
            CommandHandler("status", status),
        ],
    )
    application.add_handler(conv_handler)
    
    # Set up post init hook for AMI connection
    application.post_init = post_init
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 