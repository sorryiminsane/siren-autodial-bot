import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes
)
from panoramisk import Manager as AMIManager
from utils.ari_manager import ARIManager
from commands.call_manager import CallManager
from handlers.conversation import ConversationStates, handle_conversation
from handlers.commands import register_command_handlers

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))

# AMI Configuration
AMI_HOST = os.getenv("AMI_HOST", "127.0.0.1")
AMI_PORT = int(os.getenv("AMI_PORT", "5038"))
AMI_USERNAME = os.getenv("AMI_USERNAME", "user")
AMI_SECRET = os.getenv("AMI_SECRET", "secret")

# ARI Configuration
ARI_URL = os.getenv("ARI_URL", "http://localhost:8088")
ARI_USERNAME = os.getenv("ARI_USERNAME", "user")
ARI_PASSWORD = os.getenv("ARI_PASSWORD", "secret")

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    error_message = "An error occurred while processing your request. Please try again later."
    
    if update.effective_message:
        await update.effective_message.reply_text(error_message)

async def setup_managers(application: Application) -> None:
    """Set up AMI and ARI managers."""
    try:
        # Initialize AMI
        ami_manager = AMIManager(
            host=AMI_HOST,
            port=AMI_PORT,
            username=AMI_USERNAME,
            secret=AMI_SECRET,
            encoding='utf8'
        )
        logger.info("Connecting to Asterisk AMI...")
        await ami_manager.connect()
        logger.info("Successfully connected to Asterisk AMI")
        
        # Initialize ARI
        ari_manager = ARIManager(
            url=ARI_URL,
            username=ARI_USERNAME,
            password=ARI_PASSWORD
        )
        logger.info("Connecting to Asterisk ARI...")
        success = await ari_manager.connect()
        if not success:
            raise Exception("Failed to connect to ARI")
        logger.info("Successfully connected to Asterisk ARI")
        
        # Initialize Call Manager
        call_manager = CallManager(ami_manager, ari_manager)
        
        # Store in application context
        application.bot_data.update({
            "ami_manager": ami_manager,
            "ari_manager": ari_manager,
            "call_manager": call_manager
        })
        
    except Exception as e:
        logger.error(f"Failed to initialize managers: {str(e)}")
        application.bot_data.update({
            "ami_manager": None,
            "ari_manager": None,
            "call_manager": None
        })

def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Register command handlers (these work without conversation)
    register_command_handlers(application)
    
    # Add conversation handler for menu navigation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("menu", handle_conversation)],
        states=ConversationStates.get_states(),
        fallbacks=[CommandHandler("menu", handle_conversation)],
    )
    application.add_handler(conv_handler)
    
    # Set up post init hook for AMI and ARI connection
    application.post_init = setup_managers
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 