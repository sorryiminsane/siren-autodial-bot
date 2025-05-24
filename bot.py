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

# Conversation states
(MAIN_MENU, SETTINGS, PHONE_SETTINGS, 
 CALL_MENU, AGENT_MANAGEMENT, AUTO_DIAL) = range(6)

# Global in-memory data structures
active_calls = {}
pending_originations = {}
uniqueid_to_call_id = {}
channel_to_call_id = {}
active_campaigns = {}

global_application_instance = None # Declare global variable for application instance

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
        async with get_db_session() as session: # <-- Async context
            # agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id)) # <-- Async query
            agent = result.scalar_one_or_none()
            if agent:
                await show_main_menu(update, context, agent) # <-- Await helper
            else:
                # Handle case where agent might not be found (though unlikely if they got here)
                await query.message.edit_text("Error retrieving agent data.")
                return ConversationHandler.END # Or MAIN_MENU
        return MAIN_MENU

    elif query.data == "make_call":
        # No DB interaction here
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
        async with get_db_session() as session: # <-- Async context
            # agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id)) # <-- Async query
            agent = result.scalar_one_or_none()
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
        # No DB interaction here
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
        # No DB interaction here (yet)
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
        async with get_db_session() as session: # <-- Async context
            # agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id)) # <-- Async query
            agent = result.scalar_one_or_none()
            if not agent:
                await query.message.edit_text("Error: Agent not found. Please try /start again.")
                return ConversationHandler.END
            await show_settings_menu(update, context, agent) # <-- Await helper
        return SETTINGS
    
    elif query.data == "manage_agents" and update.effective_user.id == SUPER_ADMIN_ID:
        # No DB interaction here (yet)
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
        async with get_db_session() as session: # <-- Async context
            # agent = session.query(Agent).filter_by(telegram_id=update.effective_user.id).first()
            result = await session.execute(select(Agent).filter_by(telegram_id=update.effective_user.id)) # <-- Async query
            agent = result.scalar_one_or_none()
            if agent:
                await show_main_menu(update, context, agent) # <-- Await helper
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
            
    elif query.data == "select_route":
        # No DB interaction needed here, just display options
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
        
    elif query.data.startswith("route_") and not query.data.startswith("confirm_route_"): # Avoid double handling
        route_key = query.data.split("_")[-1]
        route_map = {"main": "M", "red": "R", "black": "B"}
        route_display_map = {"main": "Main", "red": "Red", "black": "Black"}
        route = route_map.get(route_key)
        route_name = route_display_map.get(route_key)
        
        if not route or not route_name:
            logger.warning(f"Invalid route key: {route_key}")
            await show_settings_menu(update, context, agent) # Show settings menu again
            return SETTINGS

        # Confirmation keyboard
        keyboard = [
            [
                InlineKeyboardButton(f"✅ Yes, Switch Route", callback_data=f"confirm_route_{route}"), # Use M/R/B
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
        route = query.data.split("_")[-1] # Should be M, R, or B
        route_name_map = {"M": "Main", "R": "Red", "B": "Black"}
        route_name = route_name_map.get(route)

        if not route_name:
            logger.error(f"Invalid route confirmation data: {query.data}")
            await show_settings_menu(update, context, agent)
            return SETTINGS

        async with get_db_session() as session: # <-- New async session for modification
            try:
                # Fetch agent again within this transaction
                result = await session.execute(select(Agent).filter_by(telegram_id=user_id))
                agent_to_update = result.scalar_one_or_none()
                if agent_to_update:
                    agent_to_update.route = route
                    session.add(agent_to_update) # Add instance to session for update tracking
                    await session.commit() # Commit the change
                    agent = agent_to_update # Update the outer 'agent' variable for UI refresh
                    
                    keyboard = [[InlineKeyboardButton("🔙 Back to Settings", callback_data="back_settings")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.message.edit_text(
                        f"✅ *Route Updated Successfully*\n\n"
                        f"🌐 New Route: *{route_name}*",
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                else:
                    await query.message.edit_text("Error: Agent not found during route update.")

            except SQLAlchemyError as e:
                logger.error(f"DB Error confirming route: {e}")
                await session.rollback() # Rollback on error
                await query.message.edit_text("Database error updating route.")

        return SETTINGS # Always return SETTINGS after handling route confirmation
        
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
        
    # Handle agent management specific callbacks (list_agents, auth_agent, etc.) here
    # Add DB interactions as needed using async with get_db_session()
    
    return AGENT_MANAGEMENT

async def set_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's phone number."""
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async query
        agent = result.scalar_one_or_none()

        if not agent:
            # If agent doesn't exist, create one (similar to /start logic)
            agent = Agent(
                telegram_id=user.id,
                username=user.username,
                is_authorized=user.id == SUPER_ADMIN_ID
            )
            session.add(agent)
            await session.flush() # Flush to ensure agent object is populated if needed immediately, commit is handled by context manager
            # session.commit() # No explicit commit needed here
            
            # Reload agent might be needed if ID is used immediately after creation
            # result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # Re-fetch if needed
            # agent = result.scalar_one_or_none()
            # if not agent: # Should not happen, but safety check
            #      await update.message.reply_text("Error creating agent record.")
            #      return

        # Argument parsing and validation (no DB change)
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

        # Update phone number on the agent object
        # No need to fetch agent again if creation path wasn't taken or if re-fetched after creation
        if agent: # Ensure agent exists before updating
             agent.phone_number = phone_number
             # session.add(agent) # Not strictly needed if object came from the session
             # await session.commit() # Commit handled by context manager
             await update.message.reply_text(
                 "✅ Phone number updated successfully!\n\n"
                 f"📱 New number: `{phone_number}`",
                 parse_mode='Markdown'
             )
        else:
             # This case should be rare given the creation logic above
             logger.error(f"Agent not found or created properly for user {user.id} in set_phone")
             await update.message.reply_text("Error updating phone number. Agent record issue.")

async def set_caller_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's outbound caller ID."""
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async query
        agent = result.scalar_one_or_none()

        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

        # Argument parsing and validation (no DB change)
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

        # Agent already fetched within this session
        try:
            if not agent.is_authorized:
                await update.message.reply_text("❌ Error: You are not authorized to set a caller ID.")
                return
            
            # Store old caller_id for history
            old_caller_id = agent.caller_id
            
            # Update caller_id
            agent.caller_id = caller_id
            session.add(agent) # Add agent for update tracking
            
            # Add to history
            from models import CallerIDHistory # Ensure import is available
            history = CallerIDHistory(
                agent_id=agent.id, # Assumes agent.id is populated (should be if fetched)
                old_caller_id=old_caller_id,
                new_caller_id=caller_id
            )
            session.add(history) # Add history object
            
            # await session.commit() # Commit handled by context manager
            
            await update.message.reply_text(
                "✅ CallerID updated successfully!\n\n"
                f"📲 New CallerID: `{caller_id}`",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in set_caller_id: {str(e)}")
            # Rollback is handled by context manager
            # await session.rollback()
            await update.message.reply_text("❌ Error updating caller ID. Please try again later.")
        except AttributeError:
             # Catch potential error if agent.id isn't available (e.g., object not flushed/committed properly before history creation)
             logger.error(f"AttributeError likely agent.id missing for user {user.id} in set_caller_id")
             await update.message.reply_text("❌ Error accessing agent data for history. Please try again.")

async def set_autodial_caller_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set agent's outbound caller ID specifically for Auto-Dial campaigns."""
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async query
        agent = result.scalar_one_or_none()

        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

        # Authorization Check: Require general authorization (no DB change here)
        if not agent.is_authorized:
             await update.message.reply_text("❌ Error: You are not authorized to set configuration.")
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
    """Set agent's route."""
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return

    # Fetch agent data
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async query
        agent = result.scalar_one_or_none()

        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

        # Argument parsing and validation (no DB change)
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
        route = None # Initialize route
        
        # Convert input to proper route value (no DB change)
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

        # Agent already fetched within this session
        try:
            if not agent.is_authorized:
                await update.message.reply_text("❌ Error: You are not authorized to set a route.")
                return
            
            # Update route
            agent.route = route
            session.add(agent) # Add for update tracking
            # await session.commit() # Commit handled by context manager
            
            route_name_map = {"M": "Main", "R": "Red", "B": "Black"}
            route_name = route_name_map.get(route)
            
            await update.message.reply_text(
                f"✅ Route updated successfully!\n\n"
                f"🌐 New Route: *{route_name}*",
                parse_mode='Markdown'
            )
            
        except SQLAlchemyError as e:
            logger.error(f"Database error in set_route: {str(e)}")
            # await session.rollback() # Rollback handled by context manager
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
                if isinstance(event, dict) and event.get('Response') == 'Error':
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
    agent = None # Initialize agent
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async query
        agent = result.scalar_one_or_none()
        if not agent:
            await update.message.reply_text("❌ Error: Agent not found. Please use /start first.")
            return

        # Now we have the agent object, continue with the rest of the logic
        # Argument parsing and validation (no DB change)
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

        # Use the fetched agent object for checks and call parameters
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
            
            # Check AMI connection first (this helper should be async if it does async work, but it seems to call ami_manager which is async)
            # Assuming check_ami_status is already async as per previous structure
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
            
            # Initiate the call (originate_call helper needs to be async)
            # Assuming originate_call is already async
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
            
        except Exception as e:
            logger.error(f"Error in call command: {str(e)}")
            # Make sure status_message exists before trying to edit it
            error_text = "❌ An error occurred. Please try again later."
            if 'status_message' in locals() and status_message:
                try:
                     await status_message.edit_text(error_text)
                except Exception as edit_e:
                     logger.error(f"Failed to edit status message on error: {edit_e}")
                     await update.message.reply_text(error_text) # Fallback reply
            else:
                 await update.message.reply_text(error_text)

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

        # Define the AMI event listener function inside post_init
        async def ami_event_listener(manager, event):
            """Handle AMI events."""
            # Log all received events for debugging
            logger.debug(f"Received AMI event: {event}")
            
            # Handle call end events to clean up our tracking
            if event.name == 'Hangup':
                call_id = event.get('CallID')
                if call_id and call_id in active_calls:
                    update_call_status(call_id, 'completed', datetime.now())
            
            # Handle DTMF events
            elif event.name == 'DTMFEnd':
                call_id = event.get('CallID')
                if call_id and call_id in active_calls:
                    update_call_status(call_id, 'dtmf_received')
            
            # Handle UserEvent
            elif getattr(event, 'name', '') == 'UserEvent':
                logger.debug(f"Processing UserEvent: {dict(event)}")

                # Check if it's our specific AutoDialResponse event
                if event.get('UserEvent') == 'AutoDialResponse':
                    agent_id_str = event.get('AgentID')
                    caller_id = event.get('CallerID', 'Unknown Caller')
                    pressed_one = event.get('PressedOne')
                    campaign_id = event.get('CampaignID', 'unknown')

                    logger.info(f"Processing AutoDialResponse - AgentID: {agent_id_str}, CallerID: {caller_id}, PressedOne: {pressed_one}, CampaignID: {campaign_id}")

                    if pressed_one == 'Yes' and agent_id_str:
                        try:
                            agent_id_int = int(agent_id_str)
                            
                            # Record the response in the database
                            async with get_session() as session:
                                # Look up the campaign if it exists
                                campaign = None
                                if campaign_id != 'unknown':
                                    result = await session.execute(
                                        select(AutodialCampaign).filter_by(id=int(campaign_id))
                                    )
                                    campaign = result.scalar_one_or_none()
                                
                                # Create a response record
                                new_response = AutodialResponse(
                                    campaign_id=int(campaign_id) if campaign_id != 'unknown' else None,
                                    phone_number=caller_id,
                                    response_digit='1',
                                    timestamp=datetime.utcnow()
                                )
                                session.add(new_response)
                                await session.commit()
                                logger.info(f"Recorded response from {caller_id} in campaign {campaign_id}")
                            
                            # Build enhanced notification with campaign info
                            campaign_text = f"Campaign: {campaign.name}" if campaign else ""
                            notification_message = (
                                f"✅ *New Auto-Dial Response*\n\n"
                                f"📱 Phone: `{caller_id}`\n"
                                f"🔘 Response: Pressed 1\n"
                                f"{campaign_text}"
                            )
                            
                            # Use the application instance passed to post_init
                            logger.info(f"Attempting to send notification to agent {agent_id_int}")
                            await application.bot.send_message(
                                chat_id=agent_id_int, 
                                text=notification_message, 
                                parse_mode='Markdown'
                            )
                            logger.info(f"Sent notification to agent {agent_id_int} for call from {caller_id}")
                        except ValueError as ve:
                            logger.error(f"Value error processing response: {ve}")
                        except Exception as e:
                            logger.error(f"Failed to process response or send notification: {e}")
                    elif pressed_one != 'Yes':
                        logger.info(f"AutoDialResponse for AgentID {agent_id_str}: Called party did not press 1 (PressedOne: {pressed_one})")
                
                # Also handle the KeyPress event (alternative implementation)
                elif event.headers.get('UserEvent') == 'KeyPress':
                    number = event.headers.get('Number')
                    pressed_digit = event.headers.get('Pressed')
                    campaign_id = event.headers.get('Campaign', 'unknown')
                    
                    logger.info(f"Received KeyPress event: Number={number}, Pressed={pressed_digit}, Campaign={campaign_id}")
                    
                    if pressed_digit == '1':
                        try:
                            # Find the campaign owner
                            async with get_session() as session:
                                campaign = None
                                user_id = None
                                
                                if campaign_id != 'unknown':
                                    result = await session.execute(
                                        select(AutodialCampaign).filter_by(id=int(campaign_id))
                                    )
                                    campaign = result.scalar_one_or_none()
                                    if campaign:
                                        user_id = campaign.telegram_user_id
                                
                                # Create response record
                                new_response = AutodialResponse(
                                    campaign_id=int(campaign_id) if campaign_id != 'unknown' else None,
                                    phone_number=number,
                                    response_digit=pressed_digit,
                                    timestamp=datetime.utcnow()
                                )
                                session.add(new_response)
                                await session.commit()
                            
                            # If we found the user, notify them
                            if user_id:
                                campaign_text = f"Campaign: {campaign.name}" if campaign else ""
                                notification_message = (
                                    f"✅ *New Response*\n\n"
                                    f"📱 Phone: `{number}`\n"
                                    f"🔘 Digit: {pressed_digit}\n"
                                    f"{campaign_text}"
                                )
                                
                                await application.bot.send_message(
                                    chat_id=user_id,
                                    text=notification_message,
                                    parse_mode='Markdown'
                                )
                                logger.info(f"Sent KeyPress notification to user {user_id}")
                        except Exception as e:
                            logger.error(f"Error processing KeyPress event: {e}")
                    else:
                        logger.info(f"KeyPress event: {number} pressed {pressed_digit} (not 1)")
        
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
            
            # Check if this is a new outbound call in our autodial-ivr context
            if context == 'autodial-ivr':
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
                            call.status = "connected"
                            call.call_metadata = {
                                **(call.call_metadata or {}),
                                "connected_time": datetime.now().isoformat(),
                                "asterisk_context": context,
                                "asterisk_exten": exten
                            }
                            await session.commit()
                            
                            logger.info(f"Updated call {call.call_id} with uniqueid={uniqueid}, channel={channel}")
                            logger.info(f"Call status changed from {original_status} to connected")
                            
                            # Log detailed call info for debugging
                            logger.debug(f"Call details: Target={call.target_number}, Campaign={call.campaign_id}, Agent={call.agent_telegram_id}")
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


        # Register event listeners
        ami_manager.register_event('UserEvent', ami_event_listener)
        ami_manager.register_event('Newchannel', new_channel_event_listener)
        ami_manager.register_event('DTMFBegin', dtmf_begin_listener)  # Add DTMFBegin listener
        ami_manager.register_event('DTMFEnd', dtmf_event_listener)
        ami_manager.register_event('Hangup', hangup_event_listener) # Register Hangup event listener
        logger.info("AMI event listeners registered.")

        # Store in application context for access in handlers
        application.bot_data["ami_manager"] = ami_manager
        global_application_instance = application # Store application instance globally

    except Exception as e:
        logger.error(f"Failed to establish AMI connection or register listener: {str(e)}")
        application.bot_data["ami_manager"] = None

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
    call_id_from_event = event.get('CallID')  # Check if CallID is passed in event
    
    logger.info(f"DTMFBegin detected - Digit: {digit}, Channel: {channel}, Direction: {direction}, UniqueID: {uniqueid}, CallID: {call_id_from_event}")
    
    try:
        # Get the target number from our database
        target_number = 'Unknown'
        campaign_id = None
        agent_id = 7991166259  # Default agent ID if not found
        
        async with get_async_db_session() as session:
            call = None
            
            # Try finding the call using different methods
            if call_id_from_event:
                # First check by call_id from event variables
                call = await Call.find_by_call_id(session, call_id_from_event)
                if call:
                    logger.info(f"Found call in database by CallID: {call_id_from_event}")
            
            if not call and uniqueid:
                # Then try by Asterisk Uniqueid
                call = await Call.find_by_uniqueid(session, uniqueid)
                if call:
                    logger.info(f"Found call in database by Uniqueid: {uniqueid}")
                    
            if not call and channel:
                # Finally try by channel name
                call = await Call.find_by_channel(session, channel)
                if call:
                    logger.info(f"Found call in database by Channel: {channel}")
            
            if call:
                # Update the call status to indicate DTMF started
                target_number = call.target_number
                campaign_id = call.campaign_id
                agent_id = call.agent_telegram_id or agent_id
                
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
                logger.warning(f"Could not find call in database for Channel: {channel} or Uniqueid: {uniqueid}")

        # Get the caller ID (your number that made the call)
        caller_id = event.get('CallerIDNum') or 'Unknown Caller'
        
        logger.info(f"DTMFBegin '{digit}' pressed - Target: {target_number}, CallerID: {caller_id}, Channel: {channel}, Direction: {direction}")
        
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
        
        # Get application instance from global variable
        global application
        application = global_application_instance
        if not application:
            logger.error("Application instance not found globally. Cannot send DTMFBegin notification.")
            return

        await application.bot.send_message(
            chat_id=agent_id,
            text=notification,
            parse_mode='Markdown'
        )
        
        logger.info(f"Sent DTMFBegin notification to agent {agent_id}")
        
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

    logger.debug(f"DTMF Event: {dict(event)}")
    
    digit = event.get('Digit')
    channel = event.get('Channel')
    uniqueid = event.get('Uniqueid')
    
    # Try to get tracking information from event variables
    tracking_id = event.get('TrackingID')  # Our primary identifier JKD1.x
    call_id_from_event = event.get('CallID')  # Fallback to old CallID
    
    # Log the DTMF press for debugging
    logger.info(f"DTMF '{digit}' detected on channel {channel} (UniqueID: {uniqueid}, TrackingID: {tracking_id})")
    
    try:
        # Initialize variables
        target_number = 'Unknown'
        campaign_id = None
        caller_id = event.get('CallerIDNum') or 'Unknown Caller'
        agent_id = 7991166259  # Default agent ID
        
        # Try to get AgentTelegramID from event variables
        if 'AgentTelegramID' in event:
            try:
                agent_id = int(event['AgentTelegramID'])
                logger.info(f"Found AgentTelegramID in event: {agent_id}")
            except (ValueError, TypeError):
                logger.warning(f"Invalid AgentTelegramID in event: {event.get('AgentTelegramID')}")
        
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
        
        # Format the notification with tracking ID prominently displayed
        # Get the tracking ID from the call record if found, otherwise from event or fallback
        display_tracking_id = "Unknown"
        if call and call.tracking_id:
            display_tracking_id = call.tracking_id
            logger.info(f"Using tracking ID from database: {display_tracking_id}")
        elif tracking_id:
            display_tracking_id = tracking_id
            logger.info(f"Using tracking ID from event: {display_tracking_id}")
        
        # Format campaign display - use campaign_id if available, otherwise use tracking_id
        campaign_display = f"{campaign_id}" if campaign_id else display_tracking_id
        
        notification = (
            f"🔔 *DTMF PRESS DETECTED*\n\n"
            f"#{campaign_display}\n\n"
            f"• Target: `{target_number}`\n"
            f"• CallerID: `{caller_id}`\n"
            f"• Digit: `{digit}`\n"
            f"• Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
        )
        
        await application.bot.send_message(
            chat_id=agent_id,
            text=notification,
            parse_mode='Markdown'
        )
        
        logger.info(f"Sent DTMF notification to agent {agent_id}")
        
    except Exception as e:
        logger.error(f"Error processing DTMF event: {e}", exc_info=True)

async def hangup_event_listener(manager, event):
    """Handle Hangup events from calls using database for tracking."""
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

    logger.info(f"Hangup Event: Uniqueid={uniqueid}, Channel={channel}, TrackingID={tracking_id}, CallID={call_id_from_event}, Cause={cause}, Cause-txt={cause_txt}")

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
                    call.status = 'completed'
                    call.end_time = datetime.now()
                    
                    # Update call_metadata with hangup information
                    call.call_metadata = {
                        **(call.call_metadata or {}),
                        "hangup": {
                            "time": datetime.now().isoformat(),
                            "cause": cause,
                            "cause_txt": cause_txt,
                            "channel": channel
                        }
                    }
                    
                    await session.commit()
                    logger.info(f"Call {call.call_id} (Uniqueid: {uniqueid}) marked as completed in database")
                    
                    # Optional: Send notification to agent that call has ended
                    if call.agent_telegram_id and application:
                        try:
                            # Format campaign display - use campaign_id if available, otherwise use tracking_id
                            campaign_display = f"{call.campaign_id}" if call.campaign_id else (call.tracking_id or "Unknown")
                            
                            notification = (
                                f"🔔 *Call Ended*\n\n"
                                f"#{campaign_display}\n\n"
                                f"• Target: `{call.target_number}`\n"
                                f"• Duration: {(call.end_time - call.start_time).total_seconds():.0f} seconds\n"
                                f"• Status: Completed\n"
                                f"• Hangup Cause: {cause_txt or 'Unknown'}"
                            )
                            
                            await application.bot.send_message(
                                chat_id=call.agent_telegram_id,
                                text=notification,
                                parse_mode='Markdown'
                            )
                            logger.info(f"Sent hangup notification to agent {call.agent_telegram_id}")
                        except Exception as e:
                            logger.error(f"Failed to send hangup notification: {e}")
                else:
                    logger.debug(f"No call found in database for Uniqueid: {uniqueid}, Channel: {channel}, CallID: {call_id_from_event}")
    except Exception as e:
        logger.error(f"Error processing hangup event: {e}", exc_info=True)

async def originate_autodial_call(context: ContextTypes.DEFAULT_TYPE, target_number: str, trunk: str, caller_id: str, agent_telegram_id: int, campaign_id: Optional[int] = None, sequence_number: Optional[int] = None) -> dict:
    """Originate a call for an auto-dial campaign through Asterisk AMI using database for tracking."""
    ami_manager = context.application.bot_data.get("ami_manager")
    
    if not ami_manager:
        logger.error("AMI not connected for auto-dial")
        return {'success': False, 'message': 'AMI not connected'}
        
    try:
        # Generate a unique ID for this call
        timestamp = int(time.time())
        call_id = f"campaign_{campaign_id or 'none'}_{timestamp}"
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
        
        # Build variables string for Asterisk using double underscores for persistence across contexts
        # These variables will be available in the dialplan and DTMF events
        variables = (
            f'__AgentTelegramID={agent_telegram_id},'  # Double underscore ensures persistence
            f'__CallID={call_id},'  # Original call ID
            f'__TrackingID={tracking_id},'  # Our new primary tracking ID (e.g., JKD1.1)
            f'__SequenceNumber={sequence_number or 0},'  # Position in the campaign
            f'__OriginalTargetNumber={target_number},'  # Will persist in all contexts
            f'__CallerID={caller_id},'  # Will persist in all contexts
            f'__CampaignID={campaign_id or ""},'  # Will persist in all contexts
            f'__Origin=autodial,'  # Will persist in all contexts
            f'__ActionID={action_id}'  # Will persist in all contexts
        )
        
        logger.info(f"Originating call to {target_number} via {trunk} (Campaign: {campaign_id or 'N/A'})")
        logger.debug(f"Call variables: {variables}")
            
        # Send originate action
        # The call goes directly to the target number into the IVR context
        response = await ami_manager.send_action({
            'Action': 'Originate',
            'ActionID': action_id,
            'Channel': channel,
            'Context': 'autodial-ivr',
            'Exten': 's',
            'Priority': 1,
            'CallerID': f'"{caller_id}" <{caller_id}>',
            'Async': 'true',
            'Variable': variables,
            'Timeout': 45000,  # 45 seconds timeout
            'ChannelId': call_id  # Use call_id as ChannelId for easier tracking
        })
        
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
        if update.message:
            await update.message.reply_text("Could not identify user.")
        return None # Or ConversationHandler.END ? Let's return None for now.

    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async Query
        agent = result.scalar_one_or_none()

        if not agent or not agent.is_authorized or not agent.auto_dial:
             if update.message: # Check if update.message exists
                await update.message.reply_text(
                    "❌ You are not authorized to use the Auto-Dial feature. "
                    "Please enable it in Settings or contact an administrator."
                )
             # Attempt to show main menu if agent exists (even if unauthorized for autodial)
             try:
                if agent:
                     # Need to pass the existing agent object
                     await show_main_menu(update, context, agent) # <-- Await helper
                return MAIN_MENU # Go back to main menu if not authorized
             except Exception as e:
                 logger.error(f"Error trying to show main menu after failed auth in /autodial: {e}")
                 return ConversationHandler.END # Fallback on error showing menu

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
        if update.message: await update.message.reply_text("Could not identify user.")
        return AUTO_DIAL # Stay in state, prompt again? Or MAIN_MENU? Let's stay for now.

    agent = None # Initialize agent
    # Check authorization again
    async with get_db_session() as session: # <-- Async context
        # agent = session.query(Agent).filter_by(telegram_id=user.id).first()
        result = await session.execute(select(Agent).filter_by(telegram_id=user.id)) # <-- Async Query
        agent = result.scalar_one_or_none()

        if not agent or not agent.is_authorized or not agent.auto_dial:
            if update.message: await update.message.reply_text("❌ You are not authorized to use the Auto-Dial feature.")
            # Maybe show main menu?
            if agent: # If agent exists (but isn't authorized for autodial), show their menu
                 try:
                    await show_main_menu(update, context, agent) # <-- Await helper
                 except Exception as e:
                     logger.error(f"Error showing main menu after file auth fail: {e}")
                     return ConversationHandler.END # Fallback
            else: # If agent doesn't even exist
                 if update.message: await update.message.reply_text("Agent record not found.")
                 return ConversationHandler.END # Or MAIN_MENU?
            return MAIN_MENU # Go to main menu if not authorized here

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
        await query.message.edit_text("🔄 Initializing Auto-Dial campaign...", parse_mode='Markdown')

        # Get campaign name if provided, or generate a default one with timestamp
        campaign_name = context.user_data.get('autodial_campaign_name', f"Campaign {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        # Create a new campaign entry in the database
        campaign_id = None
        try:
            async with get_db_session() as session:
                # Create new campaign with correct model name and field name
                new_campaign = AutodialCampaign(name=campaign_name, telegram_user_id=user_id)
                session.add(new_campaign)
                await session.flush()  # Get ID before commit
                campaign_id = new_campaign.id
                await session.commit()
                logger.info(f"Created new autodial campaign with ID {campaign_id} for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to create campaign record: {e}")
            # Continue without campaign tracking if there's an error

        numbers_to_dial = context.user_data.get('autodial_numbers', [])
        if not numbers_to_dial:
            await query.message.edit_text(
                "⚠️ No numbers found to dial. Please upload a file again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]])
            )
            # Need to decide if we return AUTO_DIAL or MAIN_MENU. Let's stick to AUTO_DIAL for consistency.
            return AUTO_DIAL 

        # Get the trunk and other info needed for calls
        async with get_db_session() as session: 
            result = await session.execute(select(Agent).filter_by(telegram_id=user_id))
            agent = result.scalar_one_or_none()
            if not agent:
                await query.message.edit_text("Error: Agent data not found for making calls.")
                return MAIN_MENU
                
            # Determine actual trunk name
            asterisk_trunk_name = f"autodial-{agent.autodial_trunk}"
            
            # Update the message with campaign info
            campaign_info = f"Campaign ID: {campaign_id}\n" if campaign_id else ""
            await query.message.edit_text(
                f"🚀 Starting Auto-Dial Campaign for {len(numbers_to_dial)} numbers.\n"
                f"{campaign_info}"
                "Dialing in progress... Please wait.",
                parse_mode='Markdown'
            )
        
        # Initialize counters
        successful_originations = 0
        failed_originations = 0
        
        # Process all numbers in the list with sequence numbers for tracking
        for idx, number in enumerate(numbers_to_dial, 1):  # Start index at 1
            await asyncio.sleep(0.2)  # Throttle calls to avoid flooding
            
            logger.info(f"Dialing {number} for user {user_id}, campaign {campaign_id}, sequence {idx}")
            
            try:
                # Use our enhanced autodial function with sequence number for tracking
                result = await originate_autodial_call(
                    context=context, 
                    target_number=number, 
                    trunk=asterisk_trunk_name, 
                    caller_id=agent.autodial_caller_id, 
                    agent_telegram_id=user_id, 
                    campaign_id=campaign_id,
                    sequence_number=idx  # This is the key change - add sequence number
                )
                
                # Check result from originate_autodial_call
                if result.get('success', False):
                    successful_originations += 1
                    logger.info(f"Successfully initiated call to {number} (sequence {idx})")
                else:
                    failed_originations += 1
                    logger.error(f"Failed to initiate call to {number}: {result.get('message', 'Unknown error')}")
                    
            except Exception as e:
                failed_originations += 1
                logger.error(f"Error dialing {number}: {e}")
        
        # Clear the list from user_data
        if 'autodial_numbers' in context.user_data:
            del context.user_data['autodial_numbers']
        
        final_message = (
            "🚀 *Auto-Dial Campaign Started!*\n\n"
            "📊 *Campaign Summary*\n"
            f"• 📞 Attempted Calls: {successful_originations + failed_originations}\n"
            f"• ✅ Successful Initiations: {successful_originations}\n"
            f"• ❌ Failed Initiations: {failed_originations}\n\n"
            "🔔 *Next Steps*\n"
            "• You'll receive a notification when someone presses '1' during a call\n"
            "• Check back here for campaign updates\n"
            "• Use /status to check campaign progress\n\n"
            "_Processing calls in the background..._"
        )
        await context.bot.send_message(chat_id=user_id, text=final_message, parse_mode='Markdown')
        
        # After campaign, show main menu
        # Need to fetch agent again as the previous session is closed
        async with get_db_session() as session_after_campaign: # <-- New async context
            # agent_after_campaign = session_after_campaign.query(Agent).filter_by(telegram_id=user_id).first()
            result_after = await session_after_campaign.execute(select(Agent).filter_by(telegram_id=user_id)) # <-- Async query
            agent_after_campaign = result_after.scalar_one_or_none()
            if agent_after_campaign:
                 await show_main_menu(update, context, agent_after_campaign) # <-- Await helper
            else:
                 logger.error(f"Could not find agent {user_id} after campaign to show main menu.")
                 # Perhaps send a simple text message if menu can't be shown?
                 await context.bot.send_message(chat_id=user_id, text="Campaign finished. Use /start to see the menu.")
                 return ConversationHandler.END # End conversation if agent gone
        return MAIN_MENU
            
    logger.warning(f"Unhandled callback data in AUTO_DIAL state: {query.data}")
    return AUTO_DIAL

# In-memory storage for tracking active calls and campaigns
active_campaigns = {}  # campaign_id: [target_numbers]
active_calls = {}      # call_id: {"campaign_id": x, "target_number": y, ...}

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
    """Start the bot."""

    # Initialize database synchronously using the event loop before PTB takes over
    try:
        loop = asyncio.get_event_loop() # <--- Get loop
        loop.run_until_complete(init_db()) # <--- Run init_db using loop
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {str(e)}")
        # Optionally close loop if obtained but init failed? Check asyncio docs for best practice.
        return
    # Do not close the loop here, PTB will use it.

    # Create the Application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Register post_init callback
    application.post_init = post_init
    
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
    
    # Run the bot
    # application.run_polling() is synchronous, but it runs the async handlers correctly.
    # The Application object manages the event loop needed for the async handlers and post_init.
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # asyncio.run(main()) # <-- Previous version causing loop error
    main() # <-- Call synchronous main directly