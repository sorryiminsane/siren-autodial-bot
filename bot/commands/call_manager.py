import logging
from typing import Dict, Optional
from panoramisk import Manager as AMIManager
from ..utils.ari_manager import ARIManager
from ..conference.manager import ConferenceManager

logger = logging.getLogger(__name__)

class CallManager:
    def __init__(self, ami_manager: AMIManager, ari_manager: ARIManager):
        self.ami = ami_manager
        self.ari = ari_manager
        self.conference_manager = ConferenceManager(ari_manager)
        self.active_calls: Dict[str, Dict] = {}

    async def initiate_call(self, agent_number: str, target_number: str, trunk: str, caller_id: str) -> Dict:
        """Initiate a new call sequence."""
        try:
            # Create a conference first
            conference = await self.conference_manager.create_conference(agent_number)
            if not conference:
                return {
                    'success': False,
                    'message': 'Failed to create conference'
                }

            # Store call details
            call_id = conference.bridge_id
            self.active_calls[call_id] = {
                'agent_number': agent_number,
                'target_number': target_number,
                'conference_id': call_id,
                'status': 'initiating'
            }

            # Use AMI to originate the first leg (to agent)
            variables = {
                'CONF_ID': call_id,
                'TARGET': target_number,
                'CALLER_ID': caller_id
            }

            # Originate call to agent through AMI
            response = await self.ami.send_action({
                'Action': 'Originate',
                'Channel': f'PJSIP/{agent_number}@{trunk}',
                'Context': 'ari-conference',  # Special context for ARI handoff
                'Exten': 'start',
                'Priority': 1,
                'Callerid': f'"{caller_id}" <{caller_id}>',
                'Async': 'true',
                'Variable': ','.join([f'{k}={v}' for k, v in variables.items()]),
                'Timeout': 30000
            })

            if isinstance(response, list):
                for event in response:
                    if isinstance(event, dict) and event.get('Response') == 'Error':
                        await self.cleanup_call(call_id)
                        return {
                            'success': False,
                            'message': event.get('Message', 'Unknown error')
                        }
            
            return {
                'success': True,
                'call_id': call_id,
                'message': 'Call initiated'
            }

        except Exception as e:
            logger.error(f"Error initiating call: {str(e)}")
            if 'call_id' in locals():
                await self.cleanup_call(call_id)
            return {
                'success': False,
                'message': str(e)
            }

    async def handle_channel_entered_ari(self, channel_id: str, conf_id: str, channel_type: str):
        """Handle when a channel enters ARI context."""
        try:
            # Add to conference
            success = await self.conference_manager.add_participant(
                conf_id,
                channel_id,
                channel_type
            )

            if not success:
                logger.error(f"Failed to add {channel_type} channel to conference")
                return False

            call_info = self.active_calls.get(conf_id)
            if call_info and channel_type == "agent":
                # Agent answered, now dial target
                target_channel = await self.ari.originate_call(
                    endpoint=f'PJSIP/{call_info["target_number"]}',
                    extension='join',
                    context='ari-conference',
                    variables={
                        'CONF_ID': conf_id,
                        'CHANNEL_TYPE': 'target'
                    }
                )
                
                if not target_channel:
                    logger.error("Failed to dial target number")
                    return False

            return True

        except Exception as e:
            logger.error(f"Error handling channel in ARI: {str(e)}")
            return False

    async def handle_channel_left(self, channel_id: str, conf_id: str):
        """Handle when a channel leaves."""
        try:
            await self.conference_manager.remove_participant(conf_id, channel_id)
            
            # Check if conference is empty
            status = self.conference_manager.get_conference_status(conf_id)
            if not status or status['participants'] == 0:
                await self.cleanup_call(conf_id)
                
        except Exception as e:
            logger.error(f"Error handling channel exit: {str(e)}")

    async def cleanup_call(self, call_id: str):
        """Clean up a call and its associated resources."""
        try:
            # End conference if it exists
            await self.conference_manager.end_conference(call_id)
            
            # Remove from active calls
            if call_id in self.active_calls:
                del self.active_calls[call_id]
                
            logger.info(f"Cleaned up call {call_id}")
            
        except Exception as e:
            logger.error(f"Error cleaning up call: {str(e)}")

    def get_call_status(self, call_id: str) -> Optional[Dict]:
        """Get current status of a call."""
        call_info = self.active_calls.get(call_id)
        if not call_info:
            return None
            
        # Get conference status
        conf_status = self.conference_manager.get_conference_status(call_id)
        if conf_status:
            call_info.update(conf_status)
            
        return call_info

    async def cleanup_all(self):
        """Clean up all active calls."""
        for call_id in list(self.active_calls.keys()):
            await self.cleanup_call(call_id) 