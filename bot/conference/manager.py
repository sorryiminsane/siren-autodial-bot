import logging
import uuid
from typing import Dict, Optional, List
from ..utils.ari_manager import ARIManager

logger = logging.getLogger(__name__)

class Conference:
    def __init__(self, bridge_id: str):
        self.bridge_id = bridge_id
        self.agent_channel: Optional[str] = None
        self.target_channel: Optional[str] = None
        self.status = "initializing"  # initializing, connecting, active, ended
        self.participants: List[str] = []

class ConferenceManager:
    def __init__(self, ari_manager: ARIManager):
        self.ari = ari_manager
        self.active_conferences: Dict[str, Conference] = {}

    async def create_conference(self, agent_number: str) -> Optional[Conference]:
        """Create a new conference."""
        try:
            # Generate unique conference ID
            conf_id = str(uuid.uuid4())
            
            # Create bridge through ARI
            bridge = await self.ari.create_bridge(conf_id)
            if not bridge:
                return None
            
            # Create and store conference object
            conference = Conference(conf_id)
            self.active_conferences[conf_id] = conference
            
            logger.info(f"Created conference {conf_id} for agent {agent_number}")
            return conference
        except Exception as e:
            logger.error(f"Failed to create conference: {str(e)}")
            return None

    async def add_participant(self, conf_id: str, channel_id: str, participant_type: str) -> bool:
        """Add a participant to a conference."""
        try:
            conference = self.active_conferences.get(conf_id)
            if not conference:
                logger.error(f"Conference {conf_id} not found")
                return False

            # Add to bridge
            success = await self.ari.add_channel_to_bridge(conf_id, channel_id)
            if not success:
                return False

            # Update conference object
            if participant_type == "agent":
                conference.agent_channel = channel_id
            elif participant_type == "target":
                conference.target_channel = channel_id
            
            conference.participants.append(channel_id)
            
            # Update status if both participants are present
            if conference.agent_channel and conference.target_channel:
                conference.status = "active"
            
            logger.info(f"Added {participant_type} ({channel_id}) to conference {conf_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add participant: {str(e)}")
            return False

    async def remove_participant(self, conf_id: str, channel_id: str) -> bool:
        """Remove a participant from a conference."""
        try:
            conference = self.active_conferences.get(conf_id)
            if not conference:
                logger.error(f"Conference {conf_id} not found")
                return False

            # Remove from bridge
            success = await self.ari.remove_channel_from_bridge(conf_id, channel_id)
            if not success:
                return False

            # Update conference object
            if channel_id == conference.agent_channel:
                conference.agent_channel = None
            elif channel_id == conference.target_channel:
                conference.target_channel = None
            
            if channel_id in conference.participants:
                conference.participants.remove(channel_id)

            # If no participants left, end conference
            if not conference.participants:
                await self.end_conference(conf_id)
            else:
                conference.status = "connecting"  # Someone left but conference still exists
            
            return True
        except Exception as e:
            logger.error(f"Failed to remove participant: {str(e)}")
            return False

    async def end_conference(self, conf_id: str) -> bool:
        """End a conference and cleanup."""
        try:
            conference = self.active_conferences.get(conf_id)
            if not conference:
                return False

            # Destroy the bridge
            await self.ari.destroy_bridge(conf_id)
            
            # Update status and cleanup
            conference.status = "ended"
            del self.active_conferences[conf_id]
            
            logger.info(f"Ended conference {conf_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to end conference: {str(e)}")
            return False

    def get_conference_status(self, conf_id: str) -> Optional[Dict]:
        """Get current status of a conference."""
        conference = self.active_conferences.get(conf_id)
        if not conference:
            return None
            
        return {
            "id": conference.bridge_id,
            "status": conference.status,
            "participants": len(conference.participants),
            "has_agent": bool(conference.agent_channel),
            "has_target": bool(conference.target_channel)
        }

    async def cleanup_all(self):
        """Cleanup all active conferences."""
        for conf_id in list(self.active_conferences.keys()):
            await self.end_conference(conf_id) 