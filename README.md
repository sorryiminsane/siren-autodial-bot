# TGSip - Telegram SIP Calling Bot

A Telegram bot that enables agents to initiate outbound calls through Asterisk without requiring SIP clients.

## Setup Instructions

1. **Clone the repository**
```bash
git clone <repository-url>
cd TGSip
```

2. **Set up Python virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. **Configure PostgreSQL**
- Install PostgreSQL if not already installed
- Create a new database:
```sql
CREATE DATABASE tgsip_db;
```

4. **Environment Setup**
- Copy `.env.example` to `.env`
- Fill in the required values:
  - `TELEGRAM_BOT_TOKEN`: Get from [@BotFather](https://t.me/BotFather)
  - `DATABASE_URL`: PostgreSQL connection URL
  - `SUPER_ADMIN_ID`: Your Telegram user ID (get from [@userinfobot](https://t.me/userinfobot))

5. **Run the Bot**
```bash
python bot.py
```

## Features
- Agent authorization system
- Phone number management
- Super admin controls
- Clean menu interface

## Commands
- `/start` - Start the bot and show main menu
- `/setphone <number>` - Set your phone number (E.164 format) 