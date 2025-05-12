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
    MessageHandler,
    filters,
)
from database import init_db, get_session
from models import Agent
from sqlalchemy.exc import SQLAlchemyError
from contextlib import contextmanager
from panoramisk import Manager
import asyncio
from typing import Optional

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
 CALL_MENU, AGENT_MANAGEMENT, AUTO_DIAL) = range(6)

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
        [InlineKeyboardButton("🤖 Auto-Dial", callback_data="auto_dial")],
        [InlineKeyboardButton("📱 My Phone Number", callback_data="phone_number")],
        [InlineKeyboardButton("📊 Call History", callback_data="call_history")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
    ]
    
    if update.effective_user.id == SUPER_ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👥 Manage Agents", callback_data="manage_agents")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Get route display
    route_display = f" (Manual Route: {agent.route})" if agent.route else " (Manual Route: No Route)"
    autodial_trunk_display = f" (AutoDial Trunk: {agent.autodial_trunk or 'Not Set'})" if agent.auto_dial else ""
    
    welcome_message = (
        "🎯 *Welcome to Siren Call Center* 🎯\n\n"
        "Your professional call management solution\n"
        "─────────────────────\n\n"
        "*Status:* " + ('✅ Authorized' if agent.is_authorized else '❌ Unauthorized') + 
        (" 🤖 AutoDial: " + ("🟢 Enabled" if agent.auto_dial else "🔴 Disabled")) + "\n"
        "*Phone:* 📱 " + (agent.phone_number or 'Not set') + " _(Registered)_\n"
        "*CallerID (Manual):* 📲 " + (agent.caller_id or agent.phone_number or 'Not set') + route_display + "\n"
        "*CallerID (AutoDial):* 🤖 " + (agent.autodial_caller_id or 'Not set') + autodial_trunk_display + "\n\n"
        "*Available Commands:*\n"
        "📞 /call - Make an outbound call\n"
        "🤖 /autodial - Upload numbers for auto-dialing\n"
        "📱 /setphone - Register your phone number\n"
        "📲 /setcid - Set manual outbound caller ID\n"
        "🤖 /setautodialcid - Set Auto-Dial caller ID\n"
        "🌐 /route - Set your manual call route (M/R/B)\n"
        "⚙️ /settings - Access settings (Auto-Dial toggle, Trunks, etc.)\n"
        "📊 /history - View your call history\n"
        "ℹ️ /help - Show detailed help\n\n"
        "Please select an option from the menu below:"
    )

    if isinstance(update.callback_query, type(None)):
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.message.edit_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: Agent):
    """Displays the dynamic settings menu."""
    current_manual_route = agent.route if agent and agent.route else "Not Set"
    current_autodial_trunk = agent.autodial_trunk if agent and agent.autodial_trunk else "Not Set"

    keyboard = [
        [InlineKeyboardButton(f"🌐 Manual Route ({current_manual_route})", callback_data="select_route")],
        [InlineKeyboardButton(f"📞 Select Auto-Dial Trunk ({current_autodial_trunk})", callback_data="select_autodial_trunk")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    settings_text = (
        "⚙️ *Settings*\n\n"
        "Select an option below:\n\n"
        "• *Manual Route* - Choose route for /call\n"
        f"• *Auto-Dial Trunk* - Choose trunk for campaigns ({current_autodial_trunk})\n"
        "• More settings coming soon\n"
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
    
    # Add handling for back_main first
    if query.data == "back_main":
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            await show_main_menu(update, context, agent)
            return MAIN_MENU

    elif query.data == "make_call":
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
    
    elif query.data == "auto_dial":
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            if not agent or not agent.is_authorized or not agent.auto_dial:
                await query.message.edit_text(
                    "❌ You are not authorized to use the Auto-Dial feature. "
                    "Please enable it in Settings or contact an administrator.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
                    parse_mode='Markdown'
                )
                return MAIN_MENU

        await query.message.edit_text(
            "🤖 *Auto-Dial Setup*\n\n"
            "Please upload your .txt file containing phone numbers.\n\n"
            "File format requirements:\n"
            "• One phone number per line\n"
            "• E.164 format (e.g., +1234567890)\n"
            "• No empty lines or special characters (other than +)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]),
            parse_mode='Markdown'
        )
        return AUTO_DIAL
    
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
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            if not agent:
                # This should ideally not happen if they got to the main menu via /start
                await query.message.edit_text("Error: Agent not found. Please try /start again.")
                return ConversationHandler.END # Or MAIN_MENU if /start fixed it
            await show_settings_menu(update, context, agent)
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

    # If no known callback data is matched, perhaps show main menu again or do nothing
    # For safety, let's reshow main menu if callback data is unknown within this state
    else:
        logger.warning(f"Unhandled callback data in MAIN_MENU: {query.data}")
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            if agent:
                await show_main_menu(update, context, agent)
        return MAIN_MENU

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle settings menu interactions."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    agent = None # Define agent outside to potentially use in else block
    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user_id).first()
        if not agent: # Check agent existence early
            await query.message.edit_text("Error: Agent data not found.")
            return ConversationHandler.END

    if query.data == "back_main":
        await show_main_menu(update, context, agent) # Use existing show_main_menu
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
            agent = session.query(Agent).filter_by(telegram_id=user_id).first()
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

    # --- Auto-Dial Trunk Selection --- 
    elif query.data == "select_autodial_trunk":
        if not agent.is_authorized:
            await query.message.edit_text("❌ You are not authorized to change settings.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_settings")]])) 
            return SETTINGS
                
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
        selected_trunk = query.data.split("_")[-1] 
        trunk_name_map = {'one': 'AutoDial One', 'two': 'AutoDial Two'}
        
        # Agent already fetched and available
        if not agent.is_authorized:
            await query.message.edit_text("❌ You are not authorized.") 
            return SETTINGS
        
        # Update trunk in DB object
        agent.autodial_trunk = selected_trunk
        try: # Add try/except around commit/refresh
            session.add(agent) # Add the agent instance back to the session before commit
            session.commit()
            session.refresh(agent) # Refresh the agent object from the DB
        except SQLAlchemyError as e:
            logger.error(f"Database error saving autodial_trunk: {str(e)}")
            session.rollback()
            await query.message.edit_text(
                "❌ Error saving trunk selection. Please try again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_settings")]])
            )
            return SETTINGS # Stay in settings menu on error
            
        # Edit the current message to show confirmation briefly
        confirmation_text = f"✅ Auto-Dial Trunk set to *{trunk_name_map.get(selected_trunk, 'Unknown')}*"
        await query.message.edit_text(
             confirmation_text, 
             parse_mode='Markdown'
        )
        # Pause briefly so the user sees the confirmation (optional)
        await asyncio.sleep(1.5) 
        
        # Show updated settings menu (will edit the message again)
        # Agent object is now refreshed and reflects the saved change
        await show_settings_menu(update, context, agent)
        return SETTINGS
            
    # --- End Auto-Dial Trunk Selection ---
        
    elif query.data == "back_settings":
        # Just show the settings menu using the helper
        await show_settings_menu(update, context, agent)
        return SETTINGS
            
    # Fallback if unknown callback in Settings
    else:
        logger.warning(f"Unhandled callback data in SETTINGS: {query.data}")
        # Show settings menu again using the helper
        await show_settings_menu(update, context, agent)
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
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        if not agent:
            # If agent doesn't exist, create one (similar to /start logic)
            agent = Agent(
                telegram_id=user.id,
                username=user.username,
                is_authorized=user.id == SUPER_ADMIN_ID
            )
            session.add(agent)
            session.commit() # Commit here to get the agent object for use below
            # We might want to notify the user they've been registered
            # but for now, just proceed with setting the phone.
            # Reload agent to ensure it's bound to the session properly after potential creation
            agent = session.query(Agent).filter_by(telegram_id=user.id).first()
            if not agent: # Should not happen, but safety check
                 await update.message.reply_text("Error creating agent record.")
                 return

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

    # No need to fetch agent again, we already have it
    agent.phone_number = phone_number
    session.commit()
    await update.message.reply_text(
        "✅ Phone number updated successfully!\n\n"
        f"📱 New number: `{phone_number}`",
        parse_mode='Markdown'
    )

async def set_caller_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's outbound caller ID."""
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

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

        # Agent already fetched
        try:
            if not agent.is_authorized:
                await update.message.reply_text("❌ Error: You are not authorized to set a caller ID.")
                return
            
            # Store old caller_id for history
            old_caller_id = agent.caller_id
            
            # Update caller_id
            agent.caller_id = caller_id
            session.add(agent) # Re-add agent for update
            
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

async def set_autodial_caller_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's outbound caller ID specifically for Auto-Dial campaigns."""
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

        # Authorization Check: Require general authorization
        if not agent.is_authorized:
             await update.message.reply_text("❌ Error: You are not authorized to set configuration.")
             return

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
        
        if not validate_phone_number(autodial_caller_id):
            await update.message.reply_text(
                "❌ Invalid phone number format.\n\n"
                "Please use E.164 format:\n"
                "Example: `/setautodialcid +1234567890`",
                parse_mode='Markdown'
            )
            return

        # Agent already fetched
        try:
            # Update autodial_caller_id
            agent.autodial_caller_id = autodial_caller_id
            session.commit()
            
            await update.message.reply_text(
                "✅ Auto-Dial CallerID updated successfully!\n\n"
                f"🤖 New Auto-Dial CID: `{autodial_caller_id}`",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in set_autodial_caller_id: {str(e)}")
            session.rollback()
            await update.message.reply_text("❌ Error updating Auto-Dial caller ID. Please try again later.")

async def set_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's route."""
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

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

        # Agent already fetched
        try:
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
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

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

        # Agent already fetched
        try:
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

async def originate_autodial_call(context: ContextTypes.DEFAULT_TYPE, target_number: str, trunk: str, caller_id: str, agent_telegram_id: int) -> dict:
    """Originate a call for an auto-dial campaign through Asterisk AMI."""
    ami_manager = context.application.bot_data.get("ami_manager")
    
    if not ami_manager:
        logger.error("AMI not connected for auto-dial")
        return {'success': False, 'message': 'AMI not connected'}
        
    try:
        # Build variables string
        # AgentTelegramID will be used by Asterisk to send the UserEvent back to the correct agent
        # OriginalTargetNumber is useful for logging/tracking within Asterisk if needed
        variables = (
            f'AgentTelegramID={agent_telegram_id},'
            f'OriginalTargetNumber={target_number}'
        )
        
        # Send originate action
        # The call goes directly to the target number into the IVR context
        response = await ami_manager.send_action({
            'Action': 'Originate',
            'Channel': f'PJSIP/{target_number}@{trunk}', # Target number uses the selected autodial trunk
            'Context': 'autodial-ivr',                 # Send to the autodial IVR context
            'Exten': 's',                              # Start at the 's' extension
            'Priority': 1,
            'CallerID': f'"{caller_id}" <{caller_id}>',  # Use the agent's autodial CID
            'Async': 'true',
            'Variable': variables,
            'Timeout': 45000 # Timeout for the call attempt (e.g., 45 seconds)
        })
        
        logger.info(f"Auto-dial originate action sent for {target_number} via {trunk}. Response: {response}")
        
        # Simplified success check for originate async. A successful action submission is usually enough.
        # More robust error checking might involve listening for specific failure events if needed.
        if isinstance(response, list): # panoramisk can return a list
            for event_item in response:
                if isinstance(event_item, dict) and event_item.get('Response') == 'Error':
                    logger.error(f"AMI Error for {target_number}: {event_item.get('Message', 'Unknown error')}")
                    return {'success': False, 'message': event_item.get('Message', 'Unknown error')}
            return {'success': True, 'message': 'Originate action sent.'} # Assume success if no explicit error in list
        elif isinstance(response, dict) and response.get('Response') == 'Error':
            logger.error(f"AMI Error for {target_number}: {response.get('Message', 'Unknown error')}")
            return {'success': False, 'message': response.get('Message', 'Unknown error')}
        
        return {'success': True, 'message': 'Originate action sent.'}
            
    except Exception as e:
        logger.error(f"Error originating auto-dial call to {target_number}: {str(e)}")
        return {'success': False, 'message': str(e)}

async def handle_autodial_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Handles the /autodial command, prompting for file upload.
       Returns AUTO_DIAL state if authorized, otherwise None.
    """
    user = update.effective_user
    if not user:
        if update.message:
            await update.message.reply_text("Could not identify user.")
        return None 

    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        if not agent or not agent.is_authorized or not agent.auto_dial:
             if update.message: # Check if update.message exists
                await update.message.reply_text(
                    "❌ You are not authorized to use the Auto-Dial feature. "
                    "Please enable it in Settings or contact an administrator."
                )
             # Attempt to show main menu if agent exists
             try:
                if agent:
                     await show_main_menu(update, context, agent)
                return MAIN_MENU
             except Exception as e:
                 logger.error(f"Error trying to show main menu after failed auth in /autodial: {e}")
                 return ConversationHandler.END # Fallback

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
        if update.message: await update.message.reply_text("Could not identify user.")
        return AUTO_DIAL # Stay in state, prompt again? Or MAIN_MENU? Let's stay for now.

    # Check authorization again
    with get_db_session() as session:
        agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        if not agent or not agent.is_authorized or not agent.auto_dial:
            if update.message: await update.message.reply_text("❌ You are not authorized to use the Auto-Dial feature.")
            # Maybe show main menu?
            if agent:
                 try:
                    await show_main_menu(update, context, agent)
                 except Exception as e:
                     logger.error(f"Error showing main menu after file auth fail: {e}")
            return MAIN_MENU # Go to main menu if not authorized here

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
        valid_numbers = []
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
            # Simplified normalization focused on E.164
            normalized = re.sub(r'[^0-9+]', '', original_line) # Remove anything not digit or +
            if not normalized.startswith('+'):
                if len(normalized) == 11 and normalized.startswith('1'):
                    normalized = '+' + normalized # Add + to 1xxxxxxxxxx
                elif len(normalized) == 10:
                    normalized = '+1' + normalized # Add +1 to xxxxxxxxxx
                # Otherwise, if it doesn't start with +, it's invalid for E.164
                 
            if validate_phone_number(normalized):
                valid_numbers.append(normalized)
            else:
                invalid_lines.append((line_num, original_line))

        if not valid_numbers:
             await update.message.reply_text(
                f"❌ Processed {processed_count} lines, but found no valid E.164 phone numbers."
                " Please check the file format and try again."
            )
             return AUTO_DIAL

        context.user_data['autodial_numbers'] = valid_numbers

        response_message = f"✅ Successfully processed file '{document.file_name}'.\n\n"
        response_message += f"• Found {len(valid_numbers)} valid numbers (out of {processed_count} non-empty lines processed).\n"
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

    if not query:
        logger.warning("handle_auto_dial called without callback query.")
        return AUTO_DIAL 
        
    await query.answer()
    
    if query.data == "back_main":
        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=user_id).first()
            if agent: 
                await show_main_menu(update, context, agent)
        return MAIN_MENU
            
    elif query.data == "start_autodial_campaign":
        await query.message.edit_text("🔄 Initializing Auto-Dial campaign...", parse_mode='Markdown')

        numbers_to_dial = context.user_data.get('autodial_numbers', [])
        if not numbers_to_dial:
            await query.message.edit_text(
                "⚠️ No numbers found to dial. Please upload a file again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]])
            )
            return AUTO_DIAL # Stay in state, or MAIN_MENU

        with get_db_session() as session:
            agent = session.query(Agent).filter_by(telegram_id=user_id).first()
            if not agent:
                await query.message.edit_text("Error: Agent data not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]))
                return MAIN_MENU
            
            if not agent.is_authorized or not agent.auto_dial:
                await query.message.edit_text("❌ You are not authorized for Auto-Dial.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]))
                return MAIN_MENU

            if not agent.autodial_caller_id:
                await query.message.edit_text(
                    "⚠️ Auto-Dial Caller ID not set. Please set it via `/setautodialcid` or in Settings.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]))
                return MAIN_MENU

            if not agent.autodial_trunk:
                await query.message.edit_text(
                    "⚠️ Auto-Dial Trunk not selected. Please select it in Settings.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]))
                return MAIN_MENU
        
        # Check AMI connection
        if not await check_ami_status(context):
            await query.message.edit_text(
                "❌ Error: AMI connection is not available. Campaign cannot start.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]]))
            return MAIN_MENU

        # Determine actual trunk name (e.g., 'autodial-one' or 'autodial-two')
        # This assumes autodial_trunk stores 'one' or 'two'
        asterisk_trunk_name = f"autodial-{agent.autodial_trunk}"

        await query.message.edit_text(
            f"🚀 Starting Auto-Dial Campaign for {len(numbers_to_dial)} numbers using trunk '{asterisk_trunk_name}' and CID '{agent.autodial_caller_id}'.\n"
            "Monitor your Telegram for responses from the IVR.",
            parse_mode='Markdown'
        )

        successful_originations = 0
        failed_originations = 0

        for number in numbers_to_dial:
            # Small delay between originations to avoid overwhelming Asterisk or network
            await asyncio.sleep(0.2) # 200ms delay, adjust as needed
            
            logger.info(f"Attempting to auto-dial {number} for agent {user_id} using trunk {asterisk_trunk_name} and CID {agent.autodial_caller_id}")
            response = await originate_autodial_call(
                context,
                target_number=number,
                trunk=asterisk_trunk_name,
                caller_id=agent.autodial_caller_id,
                agent_telegram_id=user_id # Pass the agent's Telegram ID
            )
            if response['success']:
                successful_originations += 1
            else:
                failed_originations += 1
                logger.error(f"Failed to originate auto-dial for {number}: {response.get('message')}")
                # Optionally, notify user of individual failures, or summarize at the end.
                # For now, just logging.

        # Clear the list from user_data
        if 'autodial_numbers' in context.user_data:
            del context.user_data['autodial_numbers']
        
        final_message = (
            f"✅ Auto-Dial Campaign Initiated.\n\n"
            f"• Successfully initiated calls: {successful_originations}\n"
            f"• Failed to initiate calls: {failed_originations}\n\n"
            "You will be notified here if a contact presses '1' in the IVR."
        )
        # Send a new message for the final status, or edit the "Starting campaign..." one.
        # Editing might be cleaner if the list is small. For large lists, a new message is fine.
        await context.bot.send_message(chat_id=user_id, text=final_message, parse_mode='Markdown')
        
        # After campaign, show main menu
        # Need to fetch agent again if the session was closed or for a fresh object
        with get_db_session() as session_after_campaign:
            agent_after_campaign = session_after_campaign.query(Agent).filter_by(telegram_id=user_id).first()
            if agent_after_campaign:
                 await show_main_menu(update, context, agent_after_campaign)
        return MAIN_MENU
            
    logger.warning(f"Unhandled callback data in AUTO_DIAL state: {query.data}")
    return AUTO_DIAL

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
    
    # --- Define Handlers to be separated ---
    setphone_handler = CommandHandler("setphone", set_phone)
    setcid_handler = CommandHandler("setcid", set_caller_id)
    setautodialcid_handler = CommandHandler("setautodialcid", set_autodial_caller_id)
    route_handler = CommandHandler("route", set_route)
    call_handler = CommandHandler("call", call)
    status_handler = CommandHandler("status", status)

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
            AUTO_DIAL: [
                CallbackQueryHandler(handle_auto_dial), # Handles button presses like 'start_autodial_campaign' and 'back_main'
                MessageHandler(filters.Document.TEXT, handle_auto_dial_file) # Handles the file upload
            ],
        },
        fallbacks=[
            CommandHandler("start", start), 
            CommandHandler("setphone", set_phone),
            CommandHandler("setcid", set_caller_id),
            CommandHandler("route", set_route),
            CommandHandler("call", call),
        ],
        allow_reentry=True
    )
    application.add_handler(conv_handler)

    # --- Add separated command handlers directly (for use outside conversation) ---
    application.add_handler(setphone_handler)
    application.add_handler(setcid_handler)
    application.add_handler(setautodialcid_handler)
    application.add_handler(route_handler)
    application.add_handler(call_handler)
    application.add_handler(status_handler)
    
    # Set up post init hook for AMI connection
    application.post_init = post_init
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 