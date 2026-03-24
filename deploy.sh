#!/bin/bash
# ============================================================
# deploy.sh — One-shot AWS EC2 setup script
# Run this on a fresh Ubuntu 22.04 t3.small (or larger)
# ============================================================

set -e
echo "🚀 Setting up Wall Street Trading Bot..."

# Update system
sudo apt-get update -y && sudo apt-get upgrade -y

# Install Python 3.11
sudo apt-get install -y python3.11 python3.11-venv python3-pip git

# Create bot user (optional security best practice)
sudo useradd -m -s /bin/bash tradingbot 2>/dev/null || true

# Clone or copy your bot files here
# git clone https://github.com/YOUR_USERNAME/trading-bot.git /home/tradingbot/trading-bot
# OR: scp -r ./trading-bot ubuntu@YOUR_EC2_IP:/home/tradingbot/

cd /home/tradingbot/trading-bot 2>/dev/null || cd ~/trading-bot

# Virtual environment
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Copy .env file
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  Edit .env with your Discord token and channel IDs: nano .env"
fi

# Install systemd service for auto-restart
sudo tee /etc/systemd/system/tradingbot.service > /dev/null <<EOF
[Unit]
Description=Wall Street Trading Discord Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=$(pwd)/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tradingbot
sudo systemctl start tradingbot

echo ""
echo "✅ Bot deployed!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status tradingbot   # Check if running"
echo "  sudo journalctl -u tradingbot -f   # Live logs"
echo "  sudo systemctl restart tradingbot  # Restart bot"
echo "  sudo systemctl stop tradingbot     # Stop bot"
echo ""
echo "⚠️  Make sure you've filled in .env with your Discord token and channel IDs!"
