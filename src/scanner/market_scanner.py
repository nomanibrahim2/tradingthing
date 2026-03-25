"""
src/scanner/market_scanner.py
Core scanner — fetches data for all watchlist tickers,
runs technical + options analysis, returns callouts.
Uses yfinance (free, no API key required).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from src.data.yfinance_client import YFinanceClient
from src.analysis.technicals import compute_signals, TechnicalSignals
from src.analysis.options_analyzer import OptionsAnalyzer, OptionCallout
from src.analysis.flow_tracker import FlowTracker
from src.chart.chart_generator import generate_chart
from config.settings import Settings

log = logging.getLogger("MarketScanner")


class MarketScanner:
    def __init__(self, settings: Settings):
        self.s         = settings
        self.yf_client = YFinanceClient(risk_free_rate=settings.RISK_FREE_RATE)
        self.analyzer  = OptionsAnalyzer(settings)
        self.flow_tracker = FlowTracker(
            window_minutes=getattr(settings, 'FLOW_TRACKER_WINDOW_MINUTES', 120)
        )

        # Dedup: don't re-send same callout within 2 hours
        self._sent_cache: dict = {}

    # ── Full scan ─────────────────────────────────────────────────────────
    async def run_full_scan(self) -> List[dict]:
        tickers    = self.s.WATCHLIST
        log.info(f"Fetching quotes for {len(tickers)} tickers...")
        quotes_raw = await self.yf_client.get_quotes(tickers)
        quote_map  = {q.get("symbol", ""): q for q in quotes_raw}

        callouts   = []
        batch_size = 5  # smaller batches to respect yfinance rate limits
        for i in range(0, len(tickers), batch_size):
            batch   = tickers[i : i + batch_size]
            tasks   = [self._analyze_ticker(sym, quote_map.get(sym)) for sym in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, list):
                    callouts.extend(res)
                elif isinstance(res, Exception):
                    log.warning(f"Ticker error: {res}")
            await asyncio.sleep(2.0)  # rate limit buffer

        callouts.sort(key=lambda x: x["callout"].confidence, reverse=True)
        log.info(f"Scan complete — {len(callouts)} callout(s).")
        return callouts

    async def _analyze_ticker(self, symbol: str, quote: Optional[dict], is_manual: bool = False) -> List[dict]:
        if not quote:
            return []
        price = float(quote.get("last") or quote.get("close") or 0)
        if price <= 0:
            return []
        if not is_manual and self._is_recently_sent(symbol):
            return []

        # Historical bars
        bars = await self.yf_client.get_historical(symbol, days_back=self.s.CHART_DAYS + 50)
        if not bars or len(bars) < 30:
            return []

        # Options chain — fetch BEFORE compute_signals so GEX can be computed
        options = await self.yf_client.get_option_chains_multi_exp(symbol, max_expirations=3)

        # Compute signals — pass options for GEX calculation
        signals = compute_signals(bars, options=options)
        if not is_manual and (not signals or signals.bias == "NEUTRAL"):
            return []

        if not is_manual and not options:
            return []

        avg_vol  = await self.yf_client.get_avg_daily_volume(symbol)
        callouts = []
        if options and signals:
            callouts = self.analyzer.analyze_chain(symbol, price, options, signals, avg_vol)
            
        if not is_manual and not callouts:
            return []

        # Chart snapshot
        chart_bytes = None
        if self.s.GENERATE_CHARTS:
            c_type = callouts[0].option_type if callouts else "call"
            c_strike = callouts[0].strike if callouts else price
            chart_bytes = generate_chart(
                symbol, bars[-60:], signals,
                c_type, c_strike
            )

        results = []
        if callouts:
            for callout in callouts:
                if not is_manual:
                    self._mark_sent(symbol)
                results.append({
                    "callout":     callout,
                    "quote":       quote,
                    "signals":     signals,
                    "chart_bytes": chart_bytes,
                })
        else:
            # We must be manual to reach here without callouts
            results.append({
                "callout":     None,
                "quote":       quote,
                "signals":     signals,
                "chart_bytes": chart_bytes,
            })
        return results

    # ── Unusual flow scan ─────────────────────────────────────────────────
    async def scan_unusual_flow(self) -> List[dict]:
        tickers    = self.s.WATCHLIST
        quotes_raw = await self.yf_client.get_quotes(tickers)
        quote_map  = {q.get("symbol", ""): q for q in quotes_raw}

        flow_alerts = []
        batch_size  = 4  # smaller batches for rate limiting
        for i in range(0, len(tickers), batch_size):
            batch   = tickers[i : i + batch_size]
            tasks   = [self._flow_for_ticker(sym, quote_map.get(sym)) for sym in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, list):
                    flow_alerts.extend(res)
            await asyncio.sleep(2.0)

        flow_alerts.sort(
            key=lambda x: x["callout"].volume * x["callout"].mid * 100,
            reverse=True
        )
        top_alerts = flow_alerts[:10]

        # Feed the flow tracker and enrich callouts with patterns
        for item in top_alerts:
            c = item["callout"]
            self.flow_tracker.record_from_callout(
                symbol=c.symbol,
                strike=c.strike,
                option_type=c.option_type,
                expiration=c.expiration,
                volume=c.volume,
                premium=c.volume * c.mid * 100,
                classification=c.trade_classification,
                intent=c.trade_intent,
                conviction=c.conviction,
            )
            pattern = self.flow_tracker.get_flow_pattern(c.symbol, c.strike)
            if pattern:
                c.flow_pattern = pattern

        return top_alerts

    async def _flow_for_ticker(self, symbol: str, quote: Optional[dict]) -> List[dict]:
        if not quote:
            return []
        price = float(quote.get("last") or quote.get("close") or 0)
        if price <= 0:
            return []

        options = await self.yf_client.get_option_chains_multi_exp(symbol, max_expirations=4)
        if not options:
            return []

        # Try to get signals for GEX / dark pool context on flow alerts too
        signals = None
        try:
            bars = await self.yf_client.get_historical(symbol, days_back=60)
            if bars and len(bars) >= 30:
                signals = compute_signals(bars, options=options)
        except Exception:
            pass

        avg_vol  = await self.yf_client.get_avg_daily_volume(symbol)
        callouts = self.analyzer.analyze_unusual_flow(symbol, price, options, avg_vol,
                                                      signals=signals)

        return [{"callout": c, "quote": quote, "chart_bytes": None, "signals": signals}
                for c in callouts]

    # ── Single ticker ─────────────────────────────────────────────────────
    async def analyze_single(self, symbol: str) -> Optional[dict]:
        quote = await self.yf_client.get_quote(symbol)
        if not quote:
            return None
        results = await self._analyze_ticker(symbol, quote, is_manual=True)
        return results[0] if results else None

    async def get_flow_for_ticker(self, symbol: str) -> Optional[dict]:
        quote = await self.yf_client.get_quote(symbol)
        if not quote:
            return None
        results = await self._flow_for_ticker(symbol, quote)
        return results[0] if results else None

    # ── Dedup ─────────────────────────────────────────────────────────────
    def _is_recently_sent(self, symbol: str, cooldown_hours: int = 2) -> bool:
        last = self._sent_cache.get(symbol)
        if not last:
            return False
        return (datetime.utcnow() - last).total_seconds() < cooldown_hours * 3600

    def _mark_sent(self, symbol: str):
        self._sent_cache[symbol] = datetime.utcnow()
