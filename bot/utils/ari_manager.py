import logging
from ari.client import Client as ARIClient
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class ARIManager:
    def __init__(self, url: str, username: str, password: str):
        """Initialize ARI manager with connection details."""
        self.url = url
        self.username = username
        self.password = password
        self.client: Optional[ARIClient] = None
        self.stasis_app = 'conference_app'
        self.active_bridges: Dict[str, Any] = {}

    async def connect(self) -> bool:
        """Establish connection to ARI."""
        try:
            self.client = ARIClient(
                url=self.url,
                username=self.username,
                password=self.password
            )
            await self.client.connect()
            logger.info("Successfully connected to ARI")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to ARI: {str(e)}")
            return False

    async def create_bridge(self, bridge_id: str) -> Optional[Dict[str, Any]]:
        """Create a mixing bridge for conference."""
        try:
            bridge = await self.client.bridges.create(
                type='mixing',
                name=f'conf_{bridge_id}',
                bridgeId=bridge_id
            )
            self.active_bridges[bridge_id] = bridge
            logger.info(f"Created bridge {bridge_id}")
            return bridge
        except Exception as e:
            logger.error(f"Failed to create bridge: {str(e)}")
            return None

    async def add_channel_to_bridge(self, bridge_id: str, channel_id: str) -> bool:
        """Add a channel to an existing bridge."""
        try:
            bridge = self.active_bridges.get(bridge_id)
            if not bridge:
                logger.error(f"Bridge {bridge_id} not found")
                return False
            
            await bridge.add_channel(channel_id=channel_id)
            logger.info(f"Added channel {channel_id} to bridge {bridge_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add channel to bridge: {str(e)}")
            return False

    async def remove_channel_from_bridge(self, bridge_id: str, channel_id: str) -> bool:
        """Remove a channel from a bridge."""
        try:
            bridge = self.active_bridges.get(bridge_id)
            if not bridge:
                logger.error(f"Bridge {bridge_id} not found")
                return False
            
            await bridge.remove_channel(channel_id=channel_id)
            logger.info(f"Removed channel {channel_id} from bridge {bridge_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove channel from bridge: {str(e)}")
            return False

    async def destroy_bridge(self, bridge_id: str) -> bool:
        """Destroy a bridge and cleanup."""
        try:
            bridge = self.active_bridges.get(bridge_id)
            if bridge:
                await bridge.destroy()
                del self.active_bridges[bridge_id]
                logger.info(f"Destroyed bridge {bridge_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to destroy bridge: {str(e)}")
            return False

    async def originate_call(self, endpoint: str, extension: str, context: str, variables: Dict[str, str] = None) -> Optional[str]:
        """Originate a call through ARI."""
        try:
            channel = await self.client.channels.originate(
                endpoint=endpoint,
                extension=extension,
                context=context,
                variables=variables or {}
            )
            logger.info(f"Originated call to {endpoint}")
            return channel.id
        except Exception as e:
            logger.error(f"Failed to originate call: {str(e)}")
            return None

    async def cleanup(self):
        """Cleanup all active bridges."""
        for bridge_id in list(self.active_bridges.keys()):
            await self.destroy_bridge(bridge_id)
        logger.info("Cleaned up all bridges") 