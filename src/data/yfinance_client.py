"""
src/data/yfinance_client.py
Unified free data client using yfinance — replaces Tradier + Polygon.
Provides real-time quotes, historical OHLCV, and options chains.
No API key required. $0 cost.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from src.analysis.greeks import enrich_options_with_greeks

log = logging.getLogger("YFinanceClient")

# Thread pool for running blocking yfinance calls in async context
_executor = ThreadPoolExecutor(max_workers=4)

# Global rate-limit: minimum seconds between yfinance HTTP calls
_THROTTLE_DELAY = 0.35  # ~3 requests/sec to stay well under Yahoo's limit


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
        for i, sym in enumerate(symbols):
            try:
                q = self._fetch_single_quote(sym)
                if q:
                    results.append(q)
            except Exception as e:
                log.debug(f"Quote error for {sym}: {e}")
            # Throttle between individual quote fetches to avoid rate limits
            if i < len(symbols) - 1:
                time.sleep(_THROTTLE_DELAY)
        return results

    def _fetch_single_quote(self, symbol: str) -> Optional[Dict]:
        """Fetch a single quote with real-time fast_info, falling back to history()."""
        try:
            ticker = yf.Ticker(symbol)
            try:
                fi = ticker.fast_info
                rt_last = _safe_float(fi.last_price)
                rt_prev = _safe_float(fi.previous_close)
                rt_vol = int(_safe_float(fi.last_volume))
            except Exception:
                rt_last, rt_prev, rt_vol = 0.0, 0.0, 0
                
            df = ticker.history(period="5d", interval="1d")
            if df.empty or len(df) == 0:
                if rt_last <= 0:
                    return None
                return {
                    "symbol": symbol, "last": rt_last, "close": rt_prev,
                    "open": rt_last, "high": rt_last, "low": rt_last,
                    "volume": rt_vol, "change_percentage": 0.0
                }
                
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2] if len(df) > 1 else last_row
            
            # Use exact real-time instantaneous price if available
            last_val = rt_last if rt_last > 0 else _safe_float(last_row.get("Close"))
            prev_val = rt_prev if rt_prev > 0 else _safe_float(prev_row.get("Close"))
            vol_val = rt_vol if rt_vol > 0 else int(_safe_float(last_row.get("Volume")))
            
            quote = {
                "symbol": symbol,
                "last": last_val,
                "close": prev_val,
                "open": _safe_float(last_row.get("Open")),
                "high": _safe_float(last_row.get("High")),
                "low": _safe_float(last_row.get("Low")),
                "volume": vol_val,
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
        """Blocking: fetch historical daily bars with retry on rate limit."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                ticker = yf.Ticker(symbol)
                end = datetime.utcnow() + timedelta(days=1)
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
            except Exception as e:
                err_str = str(e).lower()
                if "too many requests" in err_str or "rate limit" in err_str:
                    wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    log.warning(f"Rate limited on {symbol}, retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise
        log.error(f"Failed to fetch historical for {symbol} after {max_retries} retries")
        return []

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
                spot_rt = _safe_float(ticker.fast_info.last_price)
                if spot_rt > 0:
                    spot = spot_rt
                else:
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

    # ── Corporate Catalysts (Insider) ─────────────────────────────────────
    async def get_insider_bias(self, symbol: str) -> str:
        """Fetch insider transactions and define bias based on net shares bought/sold."""
        try:
            return await self._run(self._fetch_insider_bias, symbol)
        except Exception as e:
            log.error(f"Insider bias fetch error for {symbol}: {e}")
            return "NEUTRAL"

    def _fetch_insider_bias(self, symbol: str) -> str:
        """Blocking: fetch insider bias."""
        ticker = yf.Ticker(symbol)
        try:
            df = ticker.insider_transactions
            if df is None or df.empty:
                return "NEUTRAL"
            
            # Convert column names to lowercase for robust matching
            df.columns = [str(c).lower() for c in df.columns]
            
            if 'shares' not in df.columns or 'text' not in df.columns:
                return "NEUTRAL"
                
            net_shares = 0.0
            for _, row in df.iterrows():
                shares = row.get('shares', 0)
                try:
                    import math
                    shares = float(shares)
                    if math.isnan(shares):
                        continue
                except (ValueError, TypeError):
                    continue
                    
                text_col = str(row.get('text', '')).lower()
                
                if 'purchase' in text_col or 'buy' in text_col:
                    net_shares += shares
                elif 'sale' in text_col or 'sell' in text_col:
                    net_shares -= shares
                    
            if net_shares > 0:
                return "BULLISH"
            elif net_shares < 0:
                return "BEARISH"
            else:
                return "NEUTRAL"
                
        except Exception:
            return "NEUTRAL"

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
