# Siren Call Center Bot

A professional call management solution using Telegram, Asterisk AMI, and ARI.

## Features

- Multiple route support (Main/Red/Black)
- Conference call capabilities
- Real-time call status updates
- User authorization system
- Custom caller ID support

## Requirements

- Python 3.8+
- Asterisk 16+ with AMI and ARI enabled
- PostgreSQL/SQLite database

## Installation

1. Clone the repository:
```bash
git clone https://github.com/sorryiminsane/tgpy.git
cd tgpy
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
.\venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Copy the environment template and fill in your values:
```bash
cp .env.example .env
```

5. Initialize the database:
```bash
alembic upgrade head
```

## Asterisk Configuration

1. Enable ARI in `/etc/asterisk/ari.conf`:
```ini
[general]
enabled = yes
pretty = yes

[your_ari_username]
type = user
password = your_ari_password
password_format = plain
```

2. Enable AMI in `/etc/asterisk/manager.conf`:
```ini
[general]
enabled = yes
port = 5038
bindaddr = 0.0.0.0

[your_ami_username]
secret = your_ami_password
deny=0.0.0.0/0
permit=127.0.0.1/255.255.255.0
read = all
write = all
```

3. Add ARI dialplan context in `/etc/asterisk/extensions.conf`:
```ini
[ari-conference]
exten => conference,1,NoOp(Entering ARI conference)
 same => n,Stasis(your_ari_app_name)
 same => n,Hangup()

exten => _X.,1,NoOp(Entering ARI conference for ${EXTEN})
 same => n,Stasis(your_ari_app_name,${EXTEN})
 same => n,Hangup()
```

## Usage

1. Start the bot:
```bash
python -m bot
```

2. In Telegram, start a chat with your bot and use `/start` to initialize.

3. Available commands:
- `/start` - Initialize user and show status
- `/setphone` - Set your phone number
- `/route` - Set your preferred route (M/R/B)
- `/call` - Make an outbound call

## License

MIT License 