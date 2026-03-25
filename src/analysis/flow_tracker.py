"""
src/analysis/flow_tracker.py
In-memory flow history tracker — detects repeated positioning,
sustained directional flow, and sentiment shifts across scans.

No database needed: simple dict-based cache, resets on bot restart.
Rolling window keeps only recent events (configurable, default 2 hours).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("FlowTracker")


@dataclass
class FlowEvent:
    """A single recorded flow event."""
    symbol:       str
    strike:       float
    option_type:  str       # 'call' | 'put'
    expiration:   str
    volume:       int
    premium:      float     # dollar premium (vol * mid * 100)
    classification: str
    intent:       str
    conviction:   str
    timestamp:    datetime = field(default_factory=datetime.utcnow)


class FlowTracker:
    def __init__(self, window_minutes: int = 120):
        self.window = timedelta(minutes=window_minutes)
        # symbol -> list of FlowEvent
        self._history: Dict[str, List[FlowEvent]] = defaultdict(list)

    # ── Record ────────────────────────────────────────────────────────────

    def record(self, event: FlowEvent):
        """Record a flow event and prune stale entries."""
        self._history[event.symbol].append(event)
        self._prune(event.symbol)

    def record_from_callout(
        self,
        symbol: str,
        strike: float,
        option_type: str,
        expiration: str,
        volume: int,
        premium: float,
        classification: str = "",
        intent: str = "",
        conviction: str = "",
    ):
        """Convenience: record directly from callout fields."""
        self.record(FlowEvent(
            symbol=symbol,
            strike=strike,
            option_type=option_type,
            expiration=expiration,
            volume=volume,
            premium=premium,
            classification=classification,
            intent=intent,
            conviction=conviction,
        ))

    # ── Pattern Detection ─────────────────────────────────────────────────

    def get_flow_pattern(self, symbol: str, strike: float = 0.0) -> str:
        """
        Analyze recent history for the symbol and return a pattern string.
        Returns empty string if no notable pattern detected.
        """
        self._prune(symbol)
        events = self._history.get(symbol, [])
        if len(events) < 2:
            return ""

        patterns = []

        # ── Repeated strikes ──────────────────────────────────────────────
        strike_counts: Dict[float, int] = defaultdict(int)
        for ev in events:
            strike_counts[ev.strike] += 1

        for s, count in strike_counts.items():
            if count >= 3:
                patterns.append(f"🔁 Repeated flow at ${s:.0f} strike ({count}x) — institutional accumulation")

        # Check the specific strike if provided
        if strike > 0 and strike_counts.get(strike, 0) >= 2:
            c = strike_counts[strike]
            if f"${strike:.0f}" not in " ".join(patterns):
                patterns.append(f"🔁 ${strike:.0f} strike hit {c}x in recent scans")

        # ── Directional consistency ───────────────────────────────────────
        if len(events) >= 3:
            recent = events[-5:]  # last 5 events
            call_count = sum(1 for e in recent if e.option_type == "call")
            put_count  = sum(1 for e in recent if e.option_type == "put")
            total = len(recent)

            if call_count >= total * 0.8:
                patterns.append("📈 Sustained bullish flow — consistent call activity")
            elif put_count >= total * 0.8:
                patterns.append("📉 Sustained bearish flow — consistent put activity")

        # ── Sentiment shift ───────────────────────────────────────────────
        if len(events) >= 4:
            mid = len(events) // 2
            early = events[:mid]
            late  = events[mid:]

            early_calls = sum(1 for e in early if e.option_type == "call")
            late_calls  = sum(1 for e in late  if e.option_type == "call")

            early_pct = early_calls / max(len(early), 1)
            late_pct  = late_calls  / max(len(late), 1)

            if early_pct > 0.7 and late_pct < 0.3:
                patterns.append("⚠️ Flow reversal — shifted from calls to puts")
            elif early_pct < 0.3 and late_pct > 0.7:
                patterns.append("⚠️ Flow reversal — shifted from puts to calls")

        return " | ".join(patterns) if patterns else ""

    def get_event_count(self, symbol: str) -> int:
        """Number of active events in the rolling window."""
        self._prune(symbol)
        return len(self._history.get(symbol, []))

    # ── Internal ──────────────────────────────────────────────────────────

    def _prune(self, symbol: str):
        """Remove events older than the rolling window."""
        cutoff = datetime.utcnow() - self.window
        if symbol in self._history:
            self._history[symbol] = [
                e for e in self._history[symbol] if e.timestamp > cutoff
            ]
