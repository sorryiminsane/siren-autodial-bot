"""Main entry point for the bot."""
import asyncio
import logging
import os
from dotenv import load_dotenv
from telegram.ext import Application

from .database import Base, engine
from .handlers.commands import register_command_handlers
from .utils.ari_manager import ARIManager
from .commands.call_manager import CallManager

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def main():
    """Start the bot."""
    # Load environment variables
    load_dotenv()
    
    # Create database tables
    Base.metadata.create_all(bind=engine)
    
    # Initialize bot
    application = Application.builder().token(os.getenv("BOT_TOKEN")).build()
    
    # Initialize ARI manager
    ari_manager = ARIManager(
        host=os.getenv("ARI_HOST", "localhost"),
        port=int(os.getenv("ARI_PORT", "8088")),
        username=os.getenv("ARI_USERNAME"),
        password=os.getenv("ARI_PASSWORD"),
        app_name=os.getenv("ARI_APP")
    )
    await ari_manager.connect()
    
    # Initialize call manager
    call_manager = CallManager(
        ami_host=os.getenv("AMI_HOST", "localhost"),
        ami_port=int(os.getenv("AMI_PORT", "5038")),
        ami_username=os.getenv("AMI_USERNAME"),
        ami_password=os.getenv("AMI_PASSWORD"),
        ari_manager=ari_manager
    )
    await call_manager.connect()
    
    # Store managers in bot_data
    application.bot_data["call_manager"] = call_manager
    application.bot_data["ari_manager"] = ari_manager
    application.bot_data["SUPER_ADMIN_ID"] = os.getenv("SUPER_ADMIN_ID")
    
    # Register command handlers
    register_command_handlers(application)
    
    # Start the bot
    await application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error running bot: {e}", exc_info=True) 