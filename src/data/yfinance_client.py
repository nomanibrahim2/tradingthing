"""
src/data/yfinance_client.py
Unified free data client using yfinance — replaces Tradier + Polygon.
Provides real-time quotes, historical OHLCV, and options chains.
No API key required. $0 cost.
"""

import asyncio
import logging
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from src.analysis.greeks import enrich_options_with_greeks

log = logging.getLogger("YFinanceClient")

# Thread pool for running blocking yfinance calls in async context
_executor = ThreadPoolExecutor(max_workers=4)


class YFinanceClient:
    def __init__(self, risk_free_rate: float = 0.045):
        self.risk_free_rate = risk_free_rate

    async def _run(self, func, *args, **kwargs):
        """Run a blocking function in the thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))

    # ── Quotes ────────────────────────────────────────────────────────────
    async def get_quotes(self, symbols: List[str]) -> List[Dict]:
        """Batch quote for multiple symbols."""
        if not symbols:
            return []
        try:
            return await self._run(self._fetch_quotes, symbols)
        except Exception as e:
            log.error(f"Quote fetch error: {e}")
            return []

    def _fetch_quotes(self, symbols: List[str]) -> List[Dict]:
        """Blocking: fetch quotes using individual ticker.history() calls."""
        results = []
        for sym in symbols:
            try:
                q = self._fetch_single_quote(sym)
                if q:
                    results.append(q)
            except Exception as e:
                log.debug(f"Quote error for {sym}: {e}")
        return results

    def _fetch_single_quote(self, symbol: str) -> Optional[Dict]:
        """Fetch a single quote via ticker.history() — most reliable method."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="5d", interval="1d")
            if df.empty or len(df) == 0:
                return None
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2] if len(df) > 1 else last_row
            quote = {
                "symbol": symbol,
                "last": _safe_float(last_row.get("Close")),
                "close": _safe_float(prev_row.get("Close")),
                "open": _safe_float(last_row.get("Open")),
                "high": _safe_float(last_row.get("High")),
                "low": _safe_float(last_row.get("Low")),
                "volume": int(_safe_float(last_row.get("Volume"))),
            }
            if quote["close"] and quote["close"] > 0 and quote["last"]:
                quote["change_percentage"] = round(
                    ((quote["last"] - quote["close"]) / quote["close"]) * 100, 2
                )
            else:
                quote["change_percentage"] = 0.0
            return quote
        except Exception as e:
            log.debug(f"Single quote error for {symbol}: {e}")
            return None

    async def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get a single quote."""
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    # ── Historical Prices ─────────────────────────────────────────────────
    async def get_historical(self, symbol: str, days_back: int = 100) -> List[Dict]:
        """Get daily OHLCV bars. Returns list of dicts with keys: o, h, l, c, v, t."""
        try:
            return await self._run(self._fetch_historical, symbol, days_back)
        except Exception as e:
            log.error(f"Historical fetch error for {symbol}: {e}")
            return []

    def _fetch_historical(self, symbol: str, days_back: int) -> List[Dict]:
        """Blocking: fetch historical daily bars."""
        ticker = yf.Ticker(symbol)
        end = datetime.utcnow()
        start = end - timedelta(days=days_back + 10)  # extra buffer
        df = ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
        )
        if df.empty:
            return []

        bars = []
        for idx, row in df.iterrows():
            bars.append({
                "o": _safe_float(row.get("Open")),
                "h": _safe_float(row.get("High")),
                "l": _safe_float(row.get("Low")),
                "c": _safe_float(row.get("Close")),
                "v": _safe_float(row.get("Volume")),
                "t": int(idx.timestamp() * 1000) if hasattr(idx, "timestamp") else 0,
            })
        return bars

    # ── Average Daily Volume ──────────────────────────────────────────────
    async def get_avg_daily_volume(self, symbol: str, days: int = 20) -> float:
        """Get average daily volume over N days."""
        bars = await self.get_historical(symbol, days_back=days + 5)
        if not bars:
            return 0.0
        volumes = [b.get("v", 0) for b in bars[-days:]]
        return sum(volumes) / len(volumes) if volumes else 0.0

    # ── Options ───────────────────────────────────────────────────────────
    async def get_option_expirations(self, symbol: str) -> List[str]:
        """Get available option expiration dates."""
        try:
            return await self._run(self._fetch_expirations, symbol)
        except Exception as e:
            log.error(f"Expiration fetch error for {symbol}: {e}")
            return []

    def _fetch_expirations(self, symbol: str) -> List[str]:
        """Blocking: get expirations."""
        ticker = yf.Ticker(symbol)
        try:
            expirations = ticker.options  # tuple of date strings
            return list(expirations) if expirations else []
        except Exception:
            return []

    async def get_option_chain(
        self, symbol: str, expiration: str, spot: float = None
    ) -> List[Dict]:
        """Get full options chain for one expiration with computed Greeks."""
        try:
            return await self._run(
                self._fetch_option_chain, symbol, expiration, spot
            )
        except Exception as e:
            log.error(f"Option chain error for {symbol} {expiration}: {e}")
            return []

    def _fetch_option_chain(
        self, symbol: str, expiration: str, spot: float = None
    ) -> List[Dict]:
        """Blocking: fetch option chain and compute Greeks."""
        ticker = yf.Ticker(symbol)

        if spot is None:
            try:
                hist = ticker.history(period="5d")
                if not hist.empty:
                    spot = _safe_float(hist.iloc[-1].get("Close"))
                else:
                    spot = 0
            except Exception:
                spot = 0

        if spot <= 0:
            return []

        try:
            chain = ticker.option_chain(expiration)
        except Exception as e:
            log.debug(f"Option chain unavailable for {symbol} {expiration}: {e}")
            return []

        today = date.today()
        try:
            exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        except Exception:
            exp_date = today

        dte = max(1, (exp_date - today).days)
        all_options = []

        for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                strike = _safe_float(row.get("strike"))
                bid = _safe_float(row.get("bid"))
                ask = _safe_float(row.get("ask"))
                oi = int(_safe_float(row.get("openInterest")))
                vol = int(_safe_float(row.get("volume")))
                iv = _safe_float(row.get("impliedVolatility"))
                last_price = _safe_float(row.get("lastPrice"))
                contract = str(row.get("contractSymbol", ""))

                opt = {
                    "symbol": contract,
                    "underlying": symbol,
                    "strike": strike,
                    "option_type": opt_type,
                    "expiration_date": expiration,
                    "dte": dte,
                    "bid": bid,
                    "ask": ask,
                    "last": last_price,
                    "volume": vol,
                    "open_interest": oi,
                    "impliedVolatility": iv,
                    "iv": iv,
                }
                all_options.append(opt)

        # Compute Greeks locally via Black-Scholes
        if all_options:
            enrich_options_with_greeks(all_options, spot, self.risk_free_rate)

        return all_options

    async def get_option_chains_multi_exp(
        self, symbol: str, max_expirations: int = 3
    ) -> List[Dict]:
        """Pull chains for the next N expirations and flatten."""
        expirations = await self.get_option_expirations(symbol)
        if not expirations:
            return []

        # Get spot price once for all expirations
        quote = await self.get_quote(symbol)
        spot = float(quote.get("last") or quote.get("close") or 0) if quote else 0

        expirations = expirations[:max_expirations]
        tasks = [self.get_option_chain(symbol, exp, spot=spot) for exp in expirations]
        results = await asyncio.gather(*tasks)
        all_options = []
        for chain in results:
            all_options.extend(chain)
        return all_options

    async def get_option_activity(self, symbol: str) -> List[Dict]:
        """For unusual flow — fetch chains for more expirations."""
        return await self.get_option_chains_multi_exp(symbol, max_expirations=4)

    # ── Previous Close ────────────────────────────────────────────────────
    async def get_prev_close(self, symbol: str) -> Optional[Dict]:
        """Get previous close data."""
        bars = await self.get_historical(symbol, days_back=5)
        if len(bars) >= 2:
            b = bars[-2]
            return {"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
        return None

    # ── Cleanup ───────────────────────────────────────────────────────────
    async def close(self):
        """No persistent session to close — yfinance is stateless."""
        pass


def _safe_float(value) -> float:
    """Safely convert a value to float, handling NaN and None."""
    if value is None:
        return 0.0
    try:
        import math
        f = float(value)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0
