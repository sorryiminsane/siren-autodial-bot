#!/bin/bash

# Flush existing rules
iptables -F
iptables -X

# Default policies
iptables -P INPUT ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT ACCEPT

# Allow established connections
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow loopback
iptables -A INPUT -i lo -j ACCEPT

# Rate limit SIP requests (max 10 per second from same IP)
iptables -A INPUT -p udp --dport 5060 -m hashlimit --hashlimit-above 10/sec --hashlimit-burst 20 --hashlimit-mode srcip --hashlimit-name sip -j DROP

# Block known scanner User-Agents
iptables -A INPUT -p udp --dport 5060 -m string --string "friendly-scanner" --algo bm -j DROP
iptables -A INPUT -p udp --dport 5060 -m string --string "sipcli" --algo bm -j DROP
iptables -A INPUT -p udp --dport 5060 -m string --string "VaxSIPUserAgent" --algo bm -j DROP
iptables -A INPUT -p udp --dport 5060 -m string --string "Cisco UCM" --algo bm -j DROP

# Save rules
iptables-save > /etc/iptables/rules.v4 