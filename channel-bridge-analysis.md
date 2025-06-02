# Channel Bridge Analysis

This document outlines the sequence of events from agent channel creation through bridging with the target channel in Asterisk (AMI & SIP logs). Unique IDs and field mappings are provided.

---

## 1. Agent Leg Initialization

**Timestamp**: 2025-05-29 17:55:50

**AMI Event: Newchannel**
```
Event: Newchannel
Channel: PJSIP/main-trunk-00000121
ChannelStateDesc: Down
Uniqueid: 1748541350.475
Linkedid: 1748541350.475
```

**AMI Event: Newexten (Dialplan start)**
```
Event: Newexten
Channel: PJSIP/main-trunk-00000121
Application: AppDial2 (Outgoing Line)
```

**SIP INVITE (Outgoing to trunk)**
- INVITE sip:+14802569373@... via PJSIP/main-trunk-00000121
- Call-ID: a8cc4123-f6f8-49b7-8a8e-7e83c5f91465
- 100 Trying → 183 Session Progress → 200 OK

**AMI Event: Newstate (Up)**
```
Event: Newstate
Channel: PJSIP/main-trunk-00000121
ChannelStateDesc: Up
CallerIDNum: +18009995887
```

---

## 2. Target Leg Initialization

**Timestamp**: 2025-05-29 17:55:58

**Channel Allocation**
```
PJSIP/main-trunk-00000122 allocated
Uniqueid: 1748541358.476
Linkedid: 1748541350.475
```

**SIP INVITE (Agent → Target)**
- INVITE sip:+14804855848@... via PJSIP/main-trunk-00000122
- 180 Ringing → 183 Session Progress → 200 OK (connected at 17:56:06)

**AMI Event: Newstate (Up)**
```
Event: Newstate
Channel: PJSIP/main-trunk-00000122
ChannelStateDesc: Up
CallerIDNum: outbound
ConnectedLineName: +18009995887
```

**AMI Event: DialEnd** (on primary leg)
```
Event: DialEnd
Channel: PJSIP/main-trunk-00000121
DestChannel: PJSIP/main-trunk-00000122
DialStatus: ANSWER
```

---

## 3. Bridge Creation & Join

**Timestamp**: 2025-05-29 17:56:06

1. **AMI Event: BridgeCreate**
   ```
   Event: BridgeCreate
   BridgeUniqueid: 29724890-1d85-404d-9f79-581d4169a5db
   BridgeTechnology: simple_bridge
   ```

2. **Channel Join Logs**
   - `bridge_channel_internal_join`: PJSIP/main-trunk-00000121 → simple_bridge
   - AMI BridgeEnter for PJSIP/main-trunk-00000122 (BridgeNumChannels: 1)

3. **Bridge Tech Switch**
   - simple_bridge → native_rtp (automatic upgrade)
   - Both PJSIP/main-trunk-00000121 & PJSIP/main-trunk-00000122 attach native_rtp hooks
   - native_rtp_bridge_start: Locally RTP bridged both legs

4. **AMI Event: BridgeEnter** (final)
   ```
   Event: BridgeEnter
   BridgeUniqueid: 29724890-1d85-404d-9f79-581d4169a5db
   BridgeNumChannels: 2
   Channel: PJSIP/main-trunk-00000121
   Channel: PJSIP/main-trunk-00000122
   ```

---

## 4. Key IDs & Field Mappings

- **Uniqueid**: channel-specific identifier (`1748541350.475`, `1748541358.476`)
- **Linkedid**: correlates both legs (`1748541350.475`)
- **BridgeUniqueid**: bridge session ID (`29724890-...-a5db`)
- **AMI Variables set**:
  - `BRIDGEPEER`: `PJSIP/main-trunk-00000122`
  - `BRIDGEPVTCALLID`: `559eba9c-51e9-4b32-9d1c-acf91d05b09b`

---

## 5. Detection Logic (Pseudo)

```python
# 1. On AMI Newchannel: record (Channel, Uniqueid, Linkedid)
# 2. On AMI Newstate (Up): mark channel ready
# 3. On AMI DialEnd: link primary to dest channel
# 4. On AMI BridgeCreate: new bridge_id
# 5. On AMI BridgeEnter (bridge_id): record joining channels
# 6. Final BridgeEnter with 2 channels confirms live call
```

---

*End of analysis.*
