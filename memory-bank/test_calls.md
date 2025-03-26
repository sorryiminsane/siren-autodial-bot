KittyKatDeploymentCenter*CLI> pjsip set logger on
PJSIP Logging enabled
    -- Called +14802569373@main-trunk
<--- Transmitting SIP request (1103 bytes) to UDP:207.244.247.124:5060 --->
INVITE sip:+14802569373@207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPj1f172483-e1e8-4ff3-a2f0-9432521228ff
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>
Contact: <sip:asterisk@45.45.217.116:5060>
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6146 INVITE
Allow: OPTIONS, REGISTER, SUBSCRIBE, NOTIFY, PUBLISH, INVITE, ACK, BYE, CANCEL, UPDATE, PRACK, MESSAGE, REFER
Supported: 100rel, timer, replaces, norefersub, histinfo
Session-Expires: 1800
Min-SE: 90
Remote-Party-ID: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;party=calling;privacy=off;screen=no
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Type: application/sdp
Content-Length:   263

v=0
o=- 1238866168 1238866168 IN IP4 45.45.217.116
s=Asterisk
c=IN IP4 45.45.217.116
t=0 0
m=audio 10762 RTP/AVP 8 0 101
a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16
a=ptime:20
a=maxptime:150
a=sendrecv

<--- Received SIP response (605 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 100 Trying
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPj1f172483-e1e8-4ff3-a2f0-9432521228ff;received=45.45.217.116;rport=5060
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6146 INVITE
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Session-Expires: 1800;refresher=uas
Contact: <sip:+14802569373@207.244.247.124:5060>
Content-Length: 0


<--- Received SIP response (947 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 200 OK
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPj1f172483-e1e8-4ff3-a2f0-9432521228ff;received=45.45.217.116;rport=5060
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6146 INVITE
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Session-Expires: 1800;refresher=uas
Contact: <sip:+14802569373@207.244.247.124:5060>
Content-Type: application/sdp
Require: timer
Content-Length: 282

v=0
o=root 1060022385 1060022385 IN IP4 207.244.247.124
s=Asterisk PBX 13.38.3
c=IN IP4 207.244.247.124
t=0 0
m=audio 19708 RTP/AVP 8 0 101
a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16
a=ptime:20
a=maxptime:150
a=sendrecv

       > 0x7f0abc0e95b0 -- Strict RTP learning after remote address set to: 207.244.247.124:19708
<--- Transmitting SIP request (457 bytes) to UDP:207.244.247.124:5060 --->
ACK sip:+14802569373@207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPj7c238b1b-6812-4db7-8772-d9d6fc3947f8
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6146 ACK
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Length:  0


    -- PJSIP/main-trunk-00000004 answered
    -- Executing [outbound@from-main-trunk:1] NoOp("PJSIP/main-trunk-00000004", "Starting outbound call flow") in new stack
    -- Executing [outbound@from-main-trunk:2] Set("PJSIP/main-trunk-00000004", "CALLERID(all)=+16022378447 <+16022378447>") in new stack
    -- Executing [outbound@from-main-trunk:3] Set("PJSIP/main-trunk-00000004", "CHANNEL(language)=en") in new stack
    -- Executing [outbound@from-main-trunk:4] NoOp("PJSIP/main-trunk-00000004", "Calling target: +16197248434") in new stack
    -- Executing [outbound@from-main-trunk:5] Dial("PJSIP/main-trunk-00000004", "PJSIP/+16197248434@main-trunk,60") in new stack
    -- Called PJSIP/+16197248434@main-trunk
<--- Transmitting SIP request (1100 bytes) to UDP:207.244.247.124:5060 --->
INVITE sip:+16197248434@207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPjbe1a349e-0bcd-4c6f-884b-4134bc63b5cb
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=5cca538e-b6b6-4233-9448-fcd311252f14
To: <sip:+16197248434@207.244.247.124>
Contact: <sip:asterisk@45.45.217.116:5060>
Call-ID: 96c22402-4edd-418e-b7c1-855f149c1068
CSeq: 30113 INVITE
Allow: OPTIONS, REGISTER, SUBSCRIBE, NOTIFY, PUBLISH, INVITE, ACK, BYE, CANCEL, UPDATE, PRACK, MESSAGE, REFER
Supported: 100rel, timer, replaces, norefersub, histinfo
Session-Expires: 1800
Min-SE: 90
Remote-Party-ID: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;party=calling;privacy=off;screen=no
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Type: application/sdp
Content-Length:   259

v=0
o=- 31817312 31817312 IN IP4 45.45.217.116
s=Asterisk
c=IN IP4 45.45.217.116
t=0 0
m=audio 12202 RTP/AVP 8 0 101
a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16
a=ptime:20
a=maxptime:150
a=sendrecv

<--- Transmitting SIP request (1128 bytes) to UDP:207.244.247.124:5060 --->
INVITE sip:+14802569373@207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPj5f755d4c-8af2-42f8-bfe3-b00e6cc6c160
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
Contact: <sip:asterisk@45.45.217.116:5060>
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6147 INVITE
Allow: OPTIONS, REGISTER, SUBSCRIBE, NOTIFY, PUBLISH, INVITE, ACK, BYE, CANCEL, UPDATE, PRACK, MESSAGE, REFER
Supported: 100rel, timer, replaces, norefersub, histinfo
Session-Expires: 1800;refresher=uas
Min-SE: 90
Remote-Party-ID: "+16022378447" <sip:outbound@voip2.lexotration.com>;party=calling;privacy=off;screen=no
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Type: application/sdp
Content-Length:   263

v=0
o=- 1238866168 1238866169 IN IP4 45.45.217.116
s=Asterisk
c=IN IP4 45.45.217.116
t=0 0
m=audio 10762 RTP/AVP 8 0 101
a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16
a=ptime:20
a=maxptime:150
a=sendrecv

<--- Received SIP response (606 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 100 Trying
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPjbe1a349e-0bcd-4c6f-884b-4134bc63b5cb;received=45.45.217.116;rport=5060
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=5cca538e-b6b6-4233-9448-fcd311252f14
To: <sip:+16197248434@207.244.247.124>
Call-ID: 96c22402-4edd-418e-b7c1-855f149c1068
CSeq: 30113 INVITE
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Session-Expires: 1800;refresher=uas
Contact: <sip:+16197248434@207.244.247.124:5060>
Content-Length: 0


<--- Received SIP response (620 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 100 Trying
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPj5f755d4c-8af2-42f8-bfe3-b00e6cc6c160;received=45.45.217.116;rport=5060
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6147 INVITE
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Session-Expires: 1800;refresher=uas
Contact: <sip:+14802569373@207.244.247.124:5060>
Content-Length: 0


<--- Received SIP response (947 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 200 OK
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPj5f755d4c-8af2-42f8-bfe3-b00e6cc6c160;received=45.45.217.116;rport=5060
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6147 INVITE
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Session-Expires: 1800;refresher=uas
Contact: <sip:+14802569373@207.244.247.124:5060>
Content-Type: application/sdp
Require: timer
Content-Length: 282

v=0
o=root 1060022385 1060022386 IN IP4 207.244.247.124
s=Asterisk PBX 13.38.3
c=IN IP4 207.244.247.124
t=0 0
m=audio 19708 RTP/AVP 8 0 101
a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16
a=ptime:20
a=maxptime:150
a=sendrecv

       > 0x7f0abc0e95b0 -- Strict RTP learning after remote address set to: 207.244.247.124:19708
<--- Transmitting SIP request (457 bytes) to UDP:207.244.247.124:5060 --->
ACK sip:+14802569373@207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPja9c122eb-4634-41c9-88c6-2360347d703a
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
To: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 6147 ACK
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Length:  0


<--- Received SIP response (946 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 200 OK
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPjbe1a349e-0bcd-4c6f-884b-4134bc63b5cb;received=45.45.217.116;rport=5060
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=5cca538e-b6b6-4233-9448-fcd311252f14
To: <sip:+16197248434@207.244.247.124>;tag=as0784d226
Call-ID: 96c22402-4edd-418e-b7c1-855f149c1068
CSeq: 30113 INVITE
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Session-Expires: 1800;refresher=uas
Contact: <sip:+16197248434@207.244.247.124:5060>
Content-Type: application/sdp
Require: timer
Content-Length: 280

v=0
o=root 331094964 331094964 IN IP4 207.244.247.124
s=Asterisk PBX 13.38.3
c=IN IP4 207.244.247.124
t=0 0
m=audio 14162 RTP/AVP 8 0 101
a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16
a=ptime:20
a=maxptime:150
a=sendrecv

       > 0x7f0abc085010 -- Strict RTP learning after remote address set to: 207.244.247.124:14162
<--- Transmitting SIP request (458 bytes) to UDP:207.244.247.124:5060 --->
ACK sip:+16197248434@207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPjce7c450f-d2eb-40cf-a83c-7abe8a708bb6
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=5cca538e-b6b6-4233-9448-fcd311252f14
To: <sip:+16197248434@207.244.247.124>;tag=as0784d226
Call-ID: 96c22402-4edd-418e-b7c1-855f149c1068
CSeq: 30113 ACK
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Length:  0


    -- PJSIP/main-trunk-00000005 answered PJSIP/main-trunk-00000004
    -- Channel PJSIP/main-trunk-00000005 joined 'simple_bridge' basic-bridge <1d41cdbd-8f88-4955-b66e-c04991b1fbe6>
    -- Channel PJSIP/main-trunk-00000004 joined 'simple_bridge' basic-bridge <1d41cdbd-8f88-4955-b66e-c04991b1fbe6>
       > Bridge 1d41cdbd-8f88-4955-b66e-c04991b1fbe6: switching from simple_bridge technology to native_rtp
       > Locally RTP bridged 'PJSIP/main-trunk-00000004' and 'PJSIP/main-trunk-00000005' in stack
       > 0x7f0abc0e95b0 -- Strict RTP switching to RTP target address 207.244.247.124:19708 as source
       > 0x7f0abc085010 -- Strict RTP switching to RTP target address 207.244.247.124:14162 as source
<--- Received SIP request (473 bytes) from UDP:207.244.247.124:5060 --->
BYE sip:asterisk@45.45.217.116:5060 SIP/2.0
Via: SIP/2.0/UDP 207.244.247.124:5060;branch=z9hG4bK70e91d79;rport
Max-Forwards: 70
From: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
To: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
CSeq: 102 BYE
User-Agent: MagnusBilling
X-Asterisk-HangupCause: Normal Clearing
X-Asterisk-HangupCauseCode: 16
Content-Length: 0


<--- Transmitting SIP response (401 bytes) to UDP:207.244.247.124:5060 --->
SIP/2.0 200 OK
Via: SIP/2.0/UDP 207.244.247.124:5060;rport=5060;received=207.244.247.124;branch=z9hG4bK70e91d79
Call-ID: a44dd1cf-018d-4c3c-a7f0-f7aae0bdc4b3
From: <sip:+14802569373@207.244.247.124>;tag=as7b25bea2
To: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=12c2c154-3631-4bad-99bc-5d98ed8c4334
CSeq: 102 BYE
Server: Asterisk PBX certified-18.9-cert13
Content-Length:  0


    -- Channel PJSIP/main-trunk-00000004 left 'native_rtp' basic-bridge <1d41cdbd-8f88-4955-b66e-c04991b1fbe6>
    -- Channel PJSIP/main-trunk-00000005 left 'native_rtp' basic-bridge <1d41cdbd-8f88-4955-b66e-c04991b1fbe6>
  == Spawn extension (from-main-trunk, outbound, 5) exited non-zero on 'PJSIP/main-trunk-00000004'
    -- Executing [h@from-main-trunk:1] Hangup("PJSIP/main-trunk-00000004", "") in new stack
  == Spawn extension (from-main-trunk, h, 1) exited non-zero on 'PJSIP/main-trunk-00000004'
<--- Transmitting SIP request (482 bytes) to UDP:207.244.247.124:5060 --->
BYE sip:+16197248434@207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPj51bb4870-5e79-4579-a44e-7801a090fef8
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=5cca538e-b6b6-4233-9448-fcd311252f14
To: <sip:+16197248434@207.244.247.124>;tag=as0784d226
Call-ID: 96c22402-4edd-418e-b7c1-855f149c1068
CSeq: 30114 BYE
Reason: Q.850;cause=16
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Length:  0


<--- Received SIP response (527 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 200 OK
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPj51bb4870-5e79-4579-a44e-7801a090fef8;received=45.45.217.116;rport=5060
From: "+16022378447" <sip:+16022378447@voip2.lexotration.com>;tag=5cca538e-b6b6-4233-9448-fcd311252f14
To: <sip:+16197248434@207.244.247.124>;tag=as0784d226
Call-ID: 96c22402-4edd-418e-b7c1-855f149c1068
CSeq: 30114 BYE
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Content-Length: 0


<--- Received SIP request (699 bytes) from UDP:138.124.60.132:60907 --->
INVITE sip:000048124447578@45.45.217.116 SIP/2.0
Via: SIP/2.0/UDP 138.124.60.132:60907;branch=z9hG4bK1251952375
Max-Forwards: 70
From: <sip:2302@45.45.217.116>;tag=1383016855
To: <sip:000048124447578@45.45.217.116>
Call-ID: 1524592918-403911407-1754913061
CSeq: 1 INVITE
Contact: <sip:2302@138.124.60.132:60907>
Content-Type: application/sdp
Content-Length: 208
Allow: ACK, BYE, CANCEL, INFO, INVITE, MESSAGE, NOTIFY, OPTIONS, PRACK, REFER, REGISTER, SUBSCRIBE, UPDATE, PUBLISH

v=0
o=2302 16264 18299 IN IP4 192.168.1.83
s=call
c=IN IP4 192.168.1.83
t=0 0
m=audio 25282 RTP/AVP 0 101
a=rtpmap:0 pcmu/8000
a=rtpmap:8 pcma/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-11

<--- Transmitting SIP response (506 bytes) to UDP:138.124.60.132:60907 --->
SIP/2.0 401 Unauthorized
Via: SIP/2.0/UDP 138.124.60.132:60907;rport=60907;received=138.124.60.132;branch=z9hG4bK1251952375
Call-ID: 1524592918-403911407-1754913061
From: <sip:2302@45.45.217.116>;tag=1383016855
To: <sip:000048124447578@45.45.217.116>;tag=z9hG4bK1251952375
CSeq: 1 INVITE
WWW-Authenticate: Digest realm="asterisk",nonce="1740871228/22a9e0028820cb8e1354990c50b2354a",opaque="3b84b553670775d7",algorithm=md5,qop="auth"
Server: Asterisk PBX certified-18.9-cert13
Content-Length:  0


<--- Transmitting SIP request (450 bytes) to UDP:51.79.160.73:5060 --->
OPTIONS sip:51.79.160.73:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPjbd12a342-3366-4636-acec-b70eccdb6d27
From: <sip:alternate-trunk@45.45.217.116>;tag=5521e612-1d49-4be2-b1e4-aea9160713ff
To: <sip:51.79.160.73>
Contact: <sip:alternate-trunk@45.45.217.116:5060>
Call-ID: 0282fba3-e627-4845-9df6-3fcda614a321
CSeq: 36567 OPTIONS
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Length:  0


<--- Received SIP response (527 bytes) from UDP:51.79.160.73:5060 --->
SIP/2.0 404 Not Found
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPjbd12a342-3366-4636-acec-b70eccdb6d27;received=45.45.217.116;rport=5060
From: <sip:alternate-trunk@45.45.217.116>;tag=5521e612-1d49-4be2-b1e4-aea9160713ff
To: <sip:51.79.160.73>;tag=as0ddf9ec0
Call-ID: 0282fba3-e627-4845-9df6-3fcda614a321
CSeq: 36567 OPTIONS
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Accept: application/sdp
Content-Length: 0


<--- Transmitting SIP request (453 bytes) to UDP:207.244.247.124:5060 --->
OPTIONS sip:207.244.247.124:5060 SIP/2.0
Via: SIP/2.0/UDP 45.45.217.116:5060;rport;branch=z9hG4bKPj43997716-9e4b-4491-b95e-7530e4a7407f
From: <sip:main-trunk@voip2.lexotration.com>;tag=e47aa3ce-b706-4316-8dcc-2a39f3876553
To: <sip:207.244.247.124>
Contact: <sip:main-trunk@45.45.217.116:5060>
Call-ID: ad369017-db12-494c-99b9-17dde8cec274
CSeq: 7666 OPTIONS
Max-Forwards: 70
User-Agent: Asterisk PBX certified-18.9-cert13
Content-Length:  0


<--- Received SIP response (532 bytes) from UDP:207.244.247.124:5060 --->
SIP/2.0 404 Not Found
Via: SIP/2.0/UDP 45.45.217.116:5060;branch=z9hG4bKPj43997716-9e4b-4491-b95e-7530e4a7407f;received=45.45.217.116;rport=5060
From: <sip:main-trunk@voip2.lexotration.com>;tag=e47aa3ce-b706-4316-8dcc-2a39f3876553
To: <sip:207.244.247.124>;tag=as4b42807a
Call-ID: ad369017-db12-494c-99b9-17dde8cec274
CSeq: 7666 OPTIONS
Server: MagnusBilling
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE
Supported: replaces, timer
Accept: application/sdp
Content-Length: 0


KittyKatDeploymentCenter*CLI>