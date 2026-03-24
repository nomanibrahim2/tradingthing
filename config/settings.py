"""
config/settings.py — Central configuration for the trading bot.
Copy .env.example to .env and fill in your values.
Uses yfinance (free) — no paid API keys required.
"""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # ── Discord ───────────────────────────────────────────────────────────────
    DISCORD_TOKEN: str = field(default_factory=lambda: os.getenv("DISCORD_TOKEN", ""))

    # Channel IDs — paste yours here after right-clicking each channel
    CHANNEL_CALLOUTS: int   = field(default_factory=lambda: int(os.getenv("CHANNEL_CALLOUTS", "0")))
    CHANNEL_HIGH_CONF: int  = field(default_factory=lambda: int(os.getenv("CHANNEL_HIGH_CONF", "0")))
    CHANNEL_FLOW: int       = field(default_factory=lambda: int(os.getenv("CHANNEL_FLOW", "0")))

    # ── Greeks / Risk-Free Rate ───────────────────────────────────────────────
    # Used for Black-Scholes Greeks computation (~current 10Y Treasury yield)
    RISK_FREE_RATE: float = field(default_factory=lambda: float(os.getenv("RISK_FREE_RATE", "0.045")))

    # ── Watchlist — Top 100 by volume + required tickers ─────────────────────
    WATCHLIST: List[str] = field(default_factory=lambda: [
        # Required / priority tickers
        "SPY", "QQQ", "MU", "HOOD",
        # Mega-cap tech
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD",
        "AVGO", "ORCL", "CRM", "ADBE", "INTC", "QCOM", "TXN", "AMAT",
        # Financial
        "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW", "COIN",
        # ETFs
        "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "ARKK", "SQQQ", "TQQQ",
        "UVXY", "VIX",
        # Energy
        "XOM", "CVX", "OXY", "SLB", "COP",
        # Healthcare / Biotech
        "UNH", "JNJ", "PFE", "MRNA", "ABBV", "LLY", "GILD",
        # Consumer
        "AMZN", "WMT", "TGT", "HD", "NKE", "SBUX", "MCD", "DIS",
        # High-momentum / meme
        "GME", "AMC", "PLTR", "SOFI", "RIVN", "LCID", "SNAP", "UBER",
        "LYFT", "RBLX", "DKNG", "PENN", "CLOV", "BBBY",
        # Industrial / macro
        "BA", "CAT", "DE", "LMT", "RTX", "GE", "MMM",
        # Semis / AI plays
        "SMCI", "ARM", "MRVL", "LRCX", "KLAC", "NXPI", "ON",
        # Rates / bonds
        "TLT", "HYG", "LQD",
        # Commodities proxies
        "GLD", "SLV", "USO", "UNG",
        # Other high-volume names
        "NFLX", "SPOT", "PYPL", "SQ", "ROKU", "ZM", "SNOW", "DDOG",
        "CRWD", "PANW", "OKTA", "MDB", "NET", "SHOP", "TTD",
    ])

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
