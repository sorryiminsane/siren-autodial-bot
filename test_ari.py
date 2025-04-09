import asyncio
import logging
import aioari

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def handle_channel_event(channel, event):
    logger.info(f"Received channel event: {event}")

async def main():
    try:
        client = await aioari.connect(
            'http://localhost:8088/',
            'asterisk',
            'asterisk'
        )
        logger.info("Successfully connected to ARI")

        try:
            # Subscribe to all channel events
            client.on_channel_event('StasisStart', handle_channel_event)
            client.on_channel_event('StasisEnd', handle_channel_event)
            
            # Keep the application running
            while True:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Error during event handling: {e}")
            logger.error("Full traceback:", exc_info=True)
            
    except Exception as e:
        logger.error(f"Error connecting to ARI: {e}")
        logger.error("Full traceback:", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main()) 