"""
config/settings.py — Central configuration for the trading bot.
Copy .env.example to .env and fill in your values.
Uses yfinance (free) — no paid API keys required.
"""

import json
import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()

DEFAULT_WATCHLIST = [
    # Required / priority tickers
    "SPY", "QQQ", "MU", "HOOD",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD",
    "AVGO", "ORCL", "CRM", "ADBE", "INTC", "QCOM", "TXN", "AMAT",
    # Financial
    "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW", "COIN",
    # ETFs
    "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "ARKK", "SQQQ", "TQQQ",
    "UVXY", "^VIX",
    # Energy
    "XOM", "CVX", "OXY", "SLB", "COP",
    # Healthcare / Biotech
    "UNH", "JNJ", "PFE", "MRNA", "ABBV", "LLY", "GILD",
    # Consumer
    "AMZN", "WMT", "TGT", "HD", "NKE", "SBUX", "MCD", "DIS",
    # High-momentum / meme
    "GME", "AMC", "PLTR", "SOFI", "RIVN", "LCID", "SNAP", "UBER",
    "LYFT", "RBLX", "DKNG", "PENN",
    # Industrial / macro
    "BA", "CAT", "DE", "LMT", "RTX", "GE", "MMM",
    # Semis / AI plays
    "SMCI", "ARM", "MRVL", "LRCX", "KLAC", "NXPI", "ON",
    # Rates / bonds
    "TLT", "HYG", "LQD",
    # Commodities proxies
    "GLD", "SLV", "USO", "UNG",
    # Other high-volume names
    "NFLX", "SPOT", "PYPL", "XYZ", "ROKU", "ZM", "SNOW", "DDOG",
    "CRWD", "PANW", "OKTA", "MDB", "NET", "SHOP", "TTD",
]

def load_watchlist() -> List[str]:
    filepath = os.path.join(os.path.dirname(__file__), "watchlist.json")
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            pass
    
    save_watchlist(DEFAULT_WATCHLIST)
    return list(DEFAULT_WATCHLIST)

def save_watchlist(tickers: List[str]):
    filepath = os.path.join(os.path.dirname(__file__), "watchlist.json")
    try:
        with open(filepath, "w") as f:
            json.dump(tickers, f, indent=4)
    except Exception as e:
        print(f"Error saving watchlist: {e}")



@dataclass
class Settings:
    # ── Discord ───────────────────────────────────────────────────────────────
    DISCORD_TOKEN: str = field(default_factory=lambda: os.getenv("DISCORD_TOKEN", ""))

    # Channels are now managed via !setchannel dynamically across multiple servers
    # (Removed hardcoded CHANNEL_CALLOUTS, CHANNEL_HIGH_CONF, CHANNEL_FLOW)

    # Discord Webhook configuration (optional)
    WEBHOOK_URL: str        = field(default_factory=lambda: os.getenv("WEBHOOK_URL", "https://discord.com/api/webhooks/1486088074000859237/r9xU0ScY_pzUlfTNzUTWvtT0HpCjJEC2TdnHW9XtXNVKh0clw7B8oMWOmM14BW5_oSqq"))

    # ── Greeks / Risk-Free Rate ───────────────────────────────────────────────
    # Used for Black-Scholes Greeks computation (~current 10Y Treasury yield)
    RISK_FREE_RATE: float = field(default_factory=lambda: float(os.getenv("RISK_FREE_RATE", "0.045")))

    # ── Watchlist — Top 100 by volume + required tickers ─────────────────────
    WATCHLIST: List[str] = field(default_factory=load_watchlist)

    # ── Scanner Thresholds ────────────────────────────────────────────────────
    # Options liquidity filters
    MIN_OPEN_INTEREST: int      = 500         # minimum OI to consider an option
    MAX_BID_ASK_SPREAD_PCT: float = 0.15      # max spread as % of mid price (15%)
    MIN_OPTION_VOLUME: int      = 100         # minimum daily option volume

    # Confidence tiers (0.0 – 1.0)
    HIGH_CONF_THRESHOLD: float  = 0.75        # → #high-confidence-only + #trade-callouts
    MED_CONF_THRESHOLD: float   = 0.55        # → #trade-callouts only
    # Below MED_CONF → not posted

    # Unusual flow detection
    FLOW_VOLUME_MULTIPLIER: float = 3.0       # flag if volume > 3x avg daily volume
    FLOW_MIN_PREMIUM: float     = 50_000      # minimum dollar premium to flag ($50k)

    # Technical indicator periods
    RSI_PERIOD: int             = 14
    RSI_OVERSOLD: float         = 35.0
    RSI_OVERBOUGHT: float       = 65.0
    EMA_FAST: int               = 9
    EMA_SLOW: int               = 21
    EMA_TREND: int              = 50
    MACD_FAST: int              = 12
    MACD_SLOW: int              = 26
    MACD_SIGNAL: int            = 9
    ATR_PERIOD: int             = 14
    BB_PERIOD: int              = 20
    BB_STD: float               = 2.0

    # IV vs HV filter
    IV_HV_CHEAP_THRESHOLD: float = 0.90      # IV < 90% of HV → options cheap (buy)
    IV_HV_EXPENSIVE_THRESHOLD: float = 1.30  # IV > 130% of HV → options expensive

    # Risk/reward minimum
    MIN_REWARD_RISK: float      = 2.0         # must have at least 2:1 R:R

    # Chart snapshot
    CHART_DAYS: int             = 60          # days of history to show on chart
    GENERATE_CHARTS: bool       = True        # set False to skip chart images

    # Timing
    SCAN_INTERVAL_MINUTES: int  = 10
    FLOW_INTERVAL_MINUTES: int  = 5

    # ── IV / Premium Sanity ──────────────────────────────────────────────────
    MAX_IV: float                    = 5.0    # max IV as decimal (500%) — clamp above this
    IV_RECOMPUTE_THRESHOLD: float    = 0.50   # if BS price diverges >50% from mid, recompute IV
    DUMP_PCT_THRESHOLD: float        = 0.05   # underlying drop >5% = "already dumped"
    MAX_PREMIUM_MULTIPLE: float      = 2.5    # if market mid > 2.5x theoretical, flag inflated

    # ── Flow Intelligence ─────────────────────────────────────────────────────
    FLOW_TRACKER_WINDOW_MINUTES: int = 120    # rolling window for pattern detection
    BLOCK_TRADE_MIN_CONTRACTS: int   = 1000   # min volume to classify as block trade
    LOTTERY_MAX_MID_PRICE: float     = 0.30   # max mid price for "lottery ticket"
    
    # Execution Protocol
    MAX_CONFLUENCE_ONLY: bool        = True   # Strict 100% technical/flow/insider alignment

