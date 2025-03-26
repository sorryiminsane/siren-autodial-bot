## 📌 Overview
This bot enables **agents to initiate outbound calls via Telegram**, routing them through **Asterisk**.  
Agents **do not need softphones**—calls are **handled over PSTN**, and the bot **bridges calls between agents and targets**.

## ✅ Features
- **Agents trigger calls** via `/call <number>` in Telegram.
- **No SIP clients** needed—agents use **regular phone numbers**.
- **Super Admin authorizes agents** and manages access.
- **Agent phone numbers stored in a database** (SQLite/PostgreSQL).
- **Asterisk makes two calls**: one to the agent, then to the target.
- **Number format validation** (E.164 & US formats).
- **SIP trunk authenticated via IP**.

---

## 📐 Architecture & Flow
### 🔹 Components
- **Asterisk 18.9 Certified** (Telephony Server)
- **Telegram Bot API** (Handles agent interactions)
- **SQLite/PostgreSQL** (Stores agent numbers & authorizations)
- **Asterisk AMI (Manager Interface)** (Triggers outbound calls)
- **IP-authenticated SIP trunk** (Handles outbound PSTN calls)

### 🔹 Call Flow
1. **Agent sends** `/call <target_number>` in Telegram.
2. **Bot validates the request** (checks authorization & formats number).
3. **Bot queries the database** for the agent’s registered number.
4. **Bot triggers Asterisk AMI to:**
   - **Call the agent’s registered number**.
   - **Once the agent picks up, call the target number**.
   - **Bridge the call**.
5. **Bot sends updates** on call status.

---



# 📌 Outbound Call Center Plan (Development Phase)

## **1️⃣ Agent Route Selection**
- Agents **are not assigned a route** by default.
- They **must select a route** before making calls.
- Routes are **persistent** until manually changed.

### **Route Options**
- 🌍 **Main Route**  
- 🛠️ **Development Route** *(for configuring and testing outbound dialing)*  

### **How Agents Select a Route**
- **Via Settings Menu:**
  - New button: `Select Route 🌐`
  - Options:  
    ```
    [Main Route]  🌍
    [Development Route]  🛠️
    ```
  - When a route is selected, the bot asks for confirmation:
    ```
    "Are you sure you want to switch to the Development route?  
    All outbound calls will now go through this route."
    ```
  - Confirmation buttons:
    ```
    ✅ Yes, Switch Route  
    ❌ Cancel
    ```
  - If confirmed:
    ```
    "✅ Your route has been successfully changed to [Development]!"
    ```

- **Via Command:**

/route M # Select Main Route /route D # Select Development Route


## **2️⃣ Route Display in UI**
- **The selected route will be displayed next to Caller ID** in all relevant menus.
- Example:

Caller ID: +15551234567 [D]

- **`[M]`** → Main Route  
- **`[D]`** → Development Route  

- **The selected route will NOT be displayed every time the bot starts.**

## **3️⃣ SIP Trunk Authentication**
- **Trunks authenticate via IP only** (no username/password).
- The SIP provider **must whitelist our Asterisk server's IP**.
- **No automatic failover between trunks.**