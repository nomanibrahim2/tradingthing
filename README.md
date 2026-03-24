# 📈 Wall Street Level Trading Bot

A Discord bot that scans 100+ tickers every 10 minutes, analyzes options chains with full Greeks, detects unusual flow, generates chart snapshots, and posts professional callouts to your Discord server.

**🆓 Completely free — no paid API keys required.** Uses yfinance for all market data.

---

## 🏗 Architecture

```
bot.py                        ← Entry point, Discord bot, commands
config/settings.py            ← All config, thresholds, watchlist
src/
  data/
    yfinance_client.py        ← Quotes, options chains, historical OHLCV (free)
    market_hours.py           ← NYSE/NASDAQ hours, holiday calendar
  analysis/
    technicals.py             ← EMA, SMA, RSI, MACD, ATR, BB, patterns
    greeks.py                 ← Black-Scholes Greeks calculator (local)
    options_analyzer.py       ← Strike selection, confidence scoring, TP/SL
  scanner/
    market_scanner.py         ← Orchestrates all data + analysis, batching
  discord/
    callout_sender.py         ← Rich embeds, channel routing
  chart/
    chart_generator.py        ← Candlestick + indicator chart (PNG)
```

---

## 🔑 Step 1 — Get Your Discord Bot Token

### Discord Bot Token
1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it
3. Go to **Bot** tab → **Reset Token** → copy it
4. Under **Privileged Gateway Intents** → enable **Message Content Intent**
5. Go to **OAuth2 → URL Generator** → check `bot` → check permissions:
   - Send Messages, Embed Links, Attach Files, Read Message History
6. Copy the generated URL → open it → add bot to your server

### Discord Channel IDs
1. In Discord: **Settings → Advanced → Developer Mode** → ON
2. Right-click each channel → **Copy Channel ID**
3. You need IDs for: `#trade-callouts`, `#high-confidence-only`, `#options-flow`

> **No other API keys needed!** Market data comes from yfinance (free, no key required).

---

## ⚙️ Step 2 — Configure

```bash
cp .env.example .env
nano .env   # Fill in your Discord token and channel IDs
```

---

## 🚀 Step 3 — Deploy to AWS EC2

### Launch an EC2 instance
- **AMI:** Ubuntu 22.04 LTS
- **Instance type:** t3.small ($15/mo) or t3.medium ($30/mo)
- **Security group:** Allow SSH (port 22) from your IP only
- **Storage:** 20GB gp3

### Upload and run setup
```bash
# From your local machine — copy bot files to EC2
scp -r ./trading-bot ubuntu@YOUR_EC2_PUBLIC_IP:~/

# SSH into your EC2
ssh ubuntu@YOUR_EC2_PUBLIC_IP

# Run setup script
cd ~/trading-bot
chmod +x deploy.sh
./deploy.sh
```

### Verify it's running
```bash
sudo systemctl status tradingbot
sudo journalctl -u tradingbot -f    # Live logs
```

---

## 💻 Step 4 — Run Locally (for testing)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in .env with your Discord token and channel IDs

# Run
python bot.py
```

---

## 🤖 Discord Commands

| Command | Who | Description |
|---|---|---|
| `!scan` | Admin only | Force immediate scan of all tickers |
| `!quote AAPL` | Anyone | Get analysis for one ticker right now |
| `!flow NVDA` | Anyone | Check unusual flow for a ticker |
| `!watchlist` | Anyone | Show all tickers being scanned |
| `!status` | Anyone | Bot health, market status, scan intervals |

---

## 📊 Callout Output Fields

Each callout embed includes:

**Option Details**
- Symbol, type (Call/Put), strike, expiration, DTE

**Pricing**
- Entry price (mid + slight premium), Target (TP), Stop (SL), Bid/Ask

**Risk Metrics**
- Max loss per contract, Reward:Risk ratio, Probability of profit, IV vs HV rating

**Greeks** *(computed locally via Black-Scholes)*
- Delta, Gamma, Theta, Vega

**Liquidity**
- Open interest, Option volume, Bid-ask spread %

**Technicals**
- RSI, MACD histogram, EMA 9/21/50, ATR

**Key Levels**
- Support, Resistance, Bollinger Band upper/lower

**Patterns**
- Detected chart patterns that triggered the callout

**Confidence**
- Visual bar + percentage + tier (HIGH/MEDIUM)
- 🟢 HIGH (≥75%) → #high-confidence-only + #trade-callouts
- 🟡 MEDIUM (≥55%) → #trade-callouts only
- 🔴 LOW (<55%) → not posted

**Chart Screenshot**
- 60-day candlestick chart with EMA overlays, RSI, MACD, volume

---

## ⚙️ Tuning Thresholds (config/settings.py)

| Setting | Default | Description |
|---|---|---|
| `MIN_OPEN_INTEREST` | 500 | Minimum OI to consider an option |
| `MAX_BID_ASK_SPREAD_PCT` | 15% | Max spread as % of mid |
| `FLOW_VOLUME_MULTIPLIER` | 3.0x | Flag flow if volume > 3x OI |
| `FLOW_MIN_PREMIUM` | $50,000 | Minimum dollar flow to alert |
| `HIGH_CONF_THRESHOLD` | 0.75 | Confidence cutoff for high tier |
| `MIN_REWARD_RISK` | 2.0 | Minimum R:R ratio |
| `IV_HV_CHEAP_THRESHOLD` | 0.90 | Flag options as cheap if IV < 90% HV |
| `RISK_FREE_RATE` | 0.045 | Annual rate for Black-Scholes Greeks |

---

## 📡 Data Source: yfinance

This bot uses **yfinance** to pull all market data for free:
- **Quotes:** Real-time price, volume, change %
- **Historical OHLCV:** Daily candlestick bars for technical analysis
- **Options Chains:** Full chain with bid/ask, OI, volume, IV
- **Greeks:** Computed locally using Black-Scholes (Delta, Gamma, Theta, Vega)

### Rate Limits
- yfinance scrapes Yahoo Finance — no API key needed
- ~2,000 requests/hour soft limit
- Bot uses batching + sleep intervals to stay well within limits
- Data is delayed ~15 minutes (same as most free sources)

---

## 💰 Monthly Cost Estimate

| Service | Plan | Cost |
|---|---|---|
| AWS EC2 t3.small | On-demand | ~$15/mo |
| yfinance | Free (no key) | $0 |
| **Total** | | **~$15/mo** |

Run locally instead of EC2 for **$0/mo total**.

---

## ⚠️ Disclaimer

This bot is for educational and informational purposes only.
Nothing it outputs constitutes financial advice.
Options trading involves significant risk of loss.
Always do your own research.
