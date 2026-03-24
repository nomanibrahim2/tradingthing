"""
src/data/market_hours.py
US market hours detection (NYSE/NASDAQ: 9:30–16:00 ET, Mon–Fri).
"""

from datetime import datetime, time
import pytz

ET = pytz.timezone("America/New_York")

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(16, 0)

# US Federal holidays (approximate — update annually)
HOLIDAYS_2025 = {
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
}
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
}
HOLIDAYS = HOLIDAYS_2025 | HOLIDAYS_2026


def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:           # Saturday=5, Sunday=6
        return False
    date_str = now.strftime("%Y-%m-%d")
    if date_str in HOLIDAYS:
        return False
    current_time = now.time().replace(second=0, microsecond=0)
    return MARKET_OPEN <= current_time < MARKET_CLOSE


def next_market_open() -> datetime:
    """Return next market open as ET-aware datetime."""
    now = datetime.now(ET)
    candidate = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now.time() >= MARKET_OPEN:
        from datetime import timedelta
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5 or candidate.strftime("%Y-%m-%d") in HOLIDAYS:
        from datetime import timedelta
        candidate += timedelta(days=1)
    return candidate


def is_premarket() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return time(4, 0) <= t < MARKET_OPEN


def is_afterhours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return MARKET_CLOSE <= t < time(20, 0)
