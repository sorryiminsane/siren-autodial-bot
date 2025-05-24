# Future Architecture: AMI/ARI Hybrid with Conference Bridge

## Overview
This document outlines the planned hybrid architecture using both AMI and ARI interfaces for optimal call handling and monitoring in the call center application.

## Architecture Goals
- Leverage AMI for reliable call origination and basic monitoring
- Utilize ARI for advanced call control and media handling
- Implement a conference bridge for call management
- Provide real-time status updates to Telegram

## Call Flow

### 1. Initial Call Setup (AMI)
- AMI originates call to agent
- AMI monitors call progress (ringing, answered, etc.)
- Real-time status updates sent to Telegram

### 2. Agent Connects to Conference (AMI)
- Agent answers call
- AMI places agent into a conference bridge
- Telegram receives "Agent connected to conference" update

### 3. ARI Takes Over (ARI)
- AMI passes channel control to ARI
- ARI creates new channel for target number

### 4. Second Leg Dialing (ARI)
- ARI dials target number
- ARI tracks second leg status
- Telegram receives "Dialing target: +1234567890" update

### 5. Call Bridging (ARI)
- ARI bridges target to conference
- ARI monitors call quality and DTMF
- Telegram receives "Connected to target" update

### 6. Active Call Monitoring (AMI + ARI)
- AMI monitors call duration and basic events
- ARI handles advanced features (hold, transfer, etc.)
- Telegram receives ongoing call status updates

## Technical Implementation

### AMI Responsibilities
- Call origination
- Basic call progress monitoring
- Conference bridge management
- Basic CDR collection
- System status monitoring

### ARI Responsibilities
- Advanced call control
- Media streaming and recording
- DTMF handling
- Sophisticated call routing
- Real-time call statistics

### Conference Bridge
- Managed by AMI initially
- Handed off to ARI for active call control
- Maintains call state during handoff

## Benefits
1. **Reliable Call Setup**
   - AMI's strength in basic call origination
   - No WebSocket dependency for initial call

2. **Advanced Call Control**
   - ARI's powerful features for active calls
   - Better media control and monitoring

3. **Efficient Resource Usage**
   - Only maintain ARI connections for active calls
   - Reduce WebSocket overhead

4. **Redundant Monitoring**
   - Both interfaces can monitor call status
   - Better fault tolerance

5. **Clean Separation**
   - Clear responsibilities for each interface
   - Easier debugging and maintenance

## Implementation Considerations

### Channel Handoff
- Reliable method to pass channel control
- Consider using Stasis dialplan for clean handoff
- Handle race conditions during handoff

### State Management
- Centralized call state tracking
- Handle state synchronization between AMI and ARI
- Implement timeouts and retries

### Error Handling
- Fallback mechanisms if ARI fails
- Graceful degradation of features
- Comprehensive logging

### Telegram Integration
- Single source of truth for status updates
- Avoid duplicate notifications
- Clear status messages for users

## Future Enhancements
1. Implement call recording with ARI
2. Add real-time call analytics
3. Support for video calls
4. Advanced call routing based on agent skills
5. Integration with CRM systems

## Monitoring and Maintenance
- Health checks for both AMI and ARI connections
- Performance metrics collection
- Alerting for critical failures
- Regular backup of configuration and call data
