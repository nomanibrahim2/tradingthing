"""
src/analysis/technicals.py
Full technical analysis engine — all indicators computed from OHLCV data.

Indicators included:
  Trend    : EMA 9/21/50/200, SMA 20/200, VWAP
  Momentum : RSI, Stochastic %K/%D, Williams %R, MACD, CMF
  Volatility: ATR, Bollinger Bands, Historical Vol 20d
  Volume   : OBV, ADX (+DI / -DI)
  Levels   : Support/Resistance, Pivot Points (Classic + Camarilla)
  GEX      : Gamma Exposure estimate (from options chain data)
  Dark Pool: High-volume price clusters flagged as dark pool levels
  Patterns : EMA cross, double top/bottom, flags, breakouts, pennants,
             head & shoulders (simple), inside bar, volume climax
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("Technicals")


# ── Data class ─────────────────────────────────────────────────────────────────
@dataclass
class TechnicalSignals:
    # ── Trend ──────────────────────────────────────────────────────────────
    ema9:    float = 0.0
    ema21:   float = 0.0
    ema50:   float = 0.0
    ema200:  float = 0.0
    sma20:   float = 0.0
    sma200:  float = 0.0
    vwap:    float = 0.0      # session VWAP (cumulative)

    # ── Momentum ───────────────────────────────────────────────────────────
    rsi:          float = 50.0
    stoch_k:      float = 50.0   # Stochastic %K (fast)
    stoch_d:      float = 50.0   # Stochastic %D (signal)
    prev_stoch_k: float = 50.0
    prev_stoch_d: float = 50.0
    williams_r:   float = -50.0  # Williams %R  (0 to -100)
    prev_williams_r: float = -50.0
    macd:         float = 0.0
    macd_signal:  float = 0.0
    macd_hist:    float = 0.0
    prev_macd_hist: float = 0.0
    cmf:          float = 0.0    # Chaikin Money Flow (-1 to +1)

    # ── Volatility ─────────────────────────────────────────────────────────
    atr:          float = 0.0
    atr_pct:      float = 0.0    # ATR as % of close
    bb_upper:     float = 0.0
    bb_lower:     float = 0.0
    bb_mid:       float = 0.0
    bb_width:     float = 0.0    # (upper - lower) / mid
    hist_vol_20:  float = 0.0    # 20-day annualized HV

    # ── Volume / Trend strength ────────────────────────────────────────────
    obv:          float = 0.0    # On-Balance Volume (last value)
    obv_ema:      float = 0.0    # 20-period EMA of OBV
    adx:          float = 0.0    # Average Directional Index
    plus_di:      float = 0.0    # +DI
    minus_di:     float = 0.0    # -DI

    # ── Levels ─────────────────────────────────────────────────────────────
    support:      float = 0.0
    resistance:   float = 0.0

    # Classic pivot points (daily)
    pivot:        float = 0.0
    r1:           float = 0.0
    r2:           float = 0.0
    r3:           float = 0.0
    s1:           float = 0.0
    s2:           float = 0.0
    s3:           float = 0.0

    # Camarilla pivots (tighter intraday levels)
    cam_r3:       float = 0.0
    cam_r4:       float = 0.0
    cam_s3:       float = 0.0
    cam_s4:       float = 0.0

    # ── GEX (Gamma Exposure) ───────────────────────────────────────────────
    gex:          float = 0.0        # net GEX in dollars
    gex_flip:     float = 0.0        # price where GEX flips sign
    gex_bias:     str   = "NEUTRAL"  # "LONG" | "SHORT" | "NEUTRAL"
    call_wall:    float = 0.0        # strike with highest call gamma
    put_wall:     float = 0.0        # strike with highest put gamma
    max_pain:     float = 0.0        # max pain strike

    # ── Dark Pool Levels ───────────────────────────────────────────────────
    dark_pool_levels: List[float] = field(default_factory=list)
    dark_pool_bias:   str         = "NEUTRAL"  # "BULLISH" | "BEARISH" | "NEUTRAL"

    # ── Patterns & Bias ────────────────────────────────────────────────────
    patterns:       List[str] = field(default_factory=list)
    bias:           str       = "NEUTRAL"   # BULLISH | BEARISH | NEUTRAL
    bias_score:     float     = 0.0         # -1.0 to +1.0
    trend_strength: str       = "WEAK"      # STRONG | MODERATE | WEAK


# ── Math helpers ───────────────────────────────────────────────────────────────

def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    result = np.zeros_like(prices, dtype=float)
    result[0] = prices[0]
    for i in range(1, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)
    return result


def _sma(prices: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(prices), np.nan)
    for i in range(period - 1, len(prices)):
        result[i] = prices[i - period + 1 : i + 1].mean()
    return result


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    if len(closes) <= period:
        return np.full(len(closes), 50.0)
    delta = np.diff(closes)
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros(len(closes))
    avg_loss = np.zeros(len(closes))
    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()
    for i in range(period + 1, len(closes)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
    
    with np.errstate(divide='ignore', invalid='ignore'):
        rs  = np.where(avg_loss == 0, 100.0, avg_gain / avg_loss)
    
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = 50.0
    return rsi


def _stochastic(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                k_period: int = 14, d_period: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    k = np.full(len(closes), 50.0)
    for i in range(k_period - 1, len(closes)):
        lo = lows[i - k_period + 1 : i + 1].min()
        hi = highs[i - k_period + 1 : i + 1].max()
        k[i] = ((closes[i] - lo) / (hi - lo) * 100) if hi != lo else 50.0
    d = _sma(k, d_period)
    d = np.where(np.isnan(d), 50.0, d)
    return k, d


def _williams_r(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    wr = np.full(len(closes), -50.0)
    for i in range(period - 1, len(closes)):
        hi = highs[i - period + 1 : i + 1].max()
        lo = lows[i - period + 1 : i + 1].min()
        wr[i] = ((hi - closes[i]) / (hi - lo) * -100) if hi != lo else -50.0
    return wr


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         period: int = 14) -> np.ndarray:
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]),
                   np.abs(lows[1:]  - closes[:-1])),
    )
    tr    = np.concatenate([[highs[0] - lows[0]], tr])
    atr   = np.zeros_like(closes, dtype=float)
    if period <= len(closes):
        atr[period - 1] = tr[:period].mean()
        for i in range(period, len(closes)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _bollinger(closes: np.ndarray, period: int = 20, std_dev: float = 2.0):
    mid   = _sma(closes, period)
    upper = np.full_like(closes, np.nan)
    lower = np.full_like(closes, np.nan)
    for i in range(period - 1, len(closes)):
        std      = closes[i - period + 1 : i + 1].std()
        upper[i] = mid[i] + std_dev * std
        lower[i] = mid[i] - std_dev * std
    return upper, mid, lower


def _obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    obv = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if   closes[i] > closes[i - 1]: obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]: obv[i] = obv[i - 1] - volumes[i]
        else:                            obv[i] = obv[i - 1]
    return obv


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n        = len(closes)
    plus_dm  = np.zeros(n)
    minus_dm = np.zeros(n)
    tr_arr   = np.zeros(n)
    for i in range(1, n):
        up           = highs[i]    - highs[i - 1]
        down         = lows[i - 1] - lows[i]
        plus_dm[i]   = up   if (up > down   and up   > 0) else 0.0
        minus_dm[i]  = down if (down > up   and down > 0) else 0.0
        tr_arr[i]    = max(highs[i] - lows[i],
                           abs(highs[i] - closes[i - 1]),
                           abs(lows[i]  - closes[i - 1]))

    def _smooth(arr, p):
        s    = np.zeros(n)
        s[p] = arr[1 : p + 1].sum()
        for i in range(p + 1, n):
            s[i] = s[i - 1] - s[i - 1] / p + arr[i]
        return s

    eps      = 1e-10
    tr_s     = _smooth(tr_arr,   period)
    pdm_s    = _smooth(plus_dm,  period)
    mdm_s    = _smooth(minus_dm, period)
    plus_di  = np.where(tr_s > 0, 100 * pdm_s / (tr_s + eps), 0.0)
    minus_di = np.where(tr_s > 0, 100 * mdm_s / (tr_s + eps), 0.0)
    dx       = np.where((plus_di + minus_di) > 0,
                        100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + eps), 0.0)
    adx_arr  = np.zeros(n)
    if 2 * period < n:
        adx_arr[2 * period] = dx[period : 2 * period + 1].mean()
        for i in range(2 * period + 1, n):
            adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period
    return adx_arr, plus_di, minus_di


def _cmf(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         volumes: np.ndarray, period: int = 20) -> np.ndarray:
    mfm = np.where(
        (highs - lows) != 0,
        ((closes - lows) - (highs - closes)) / (highs - lows),
        0.0,
    )
    mfv = mfm * volumes
    cmf = np.full(len(closes), 0.0)
    for i in range(period - 1, len(closes)):
        vol_sum = volumes[i - period + 1 : i + 1].sum()
        cmf[i]  = mfv[i - period + 1 : i + 1].sum() / vol_sum if vol_sum != 0 else 0.0
    return cmf


def _vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
          volumes: np.ndarray) -> np.ndarray:
    typical = (highs + lows + closes) / 3
    cumvol  = np.cumsum(volumes)
    cumtpv  = np.cumsum(typical * volumes)
    return np.where(cumvol > 0, cumtpv / cumvol, closes)


def _hist_vol(closes: np.ndarray, period: int = 20) -> float:
    if len(closes) < period + 1:
        return 0.0
    log_returns = np.diff(np.log(closes[-(period + 1):]))
    return float(log_returns.std() * math.sqrt(252))


def _pivot_points(high: float, low: float, close: float):
    p  = (high + low + close) / 3
    r1 = 2 * p - low
    s1 = 2 * p - high
    r2 = p + (high - low)
    s2 = p - (high - low)
    r3 = high + 2 * (p - low)
    s3 = low  - 2 * (high - p)
    return p, r1, r2, r3, s1, s2, s3


def _camarilla_pivots(high: float, low: float, close: float):
    rng = high - low
    r3  = close + rng * 1.1 / 4
    r4  = close + rng * 1.1 / 2
    s3  = close - rng * 1.1 / 4
    s4  = close - rng * 1.1 / 2
    return r3, r4, s3, s4


def _find_support_resistance(highs: np.ndarray, lows: np.ndarray,
                              closes: np.ndarray,
                              lookback: int = 30) -> Tuple[float, float]:
    return float(lows[-lookback:].min()), float(highs[-lookback:].max())


def _detect_dark_pool_levels(closes: np.ndarray, volumes: np.ndarray,
                              price: float,
                              n_levels: int = 5) -> Tuple[List[float], str]:
    """
    Dark pool proxy: identifies price clusters with abnormally high volume
    (2.5x average) — a practical proxy for institutional / dark pool activity.

    NOTE: Real dark pool print data requires a paid feed such as
    Unusual Whales, Quant Data, or Blackbox Stocks. This implementation
    is a volume-cluster approximation that works without additional APIs.
    """
    if len(closes) < 20:
        return [], "NEUTRAL"

    avg_vol       = volumes.mean()
    high_vol_idx  = np.where(volumes > avg_vol * 2.5)[0]
    if len(high_vol_idx) == 0:
        return [], "NEUTRAL"

    raw_levels = sorted([float(closes[i]) for i in high_vol_idx])
    threshold  = price * 0.005   # 0.5% cluster window
    clustered  = []
    cluster    = [raw_levels[0]]

    for lv in raw_levels[1:]:
        if lv - cluster[-1] <= threshold:
            cluster.append(lv)
        else:
            clustered.append(float(np.mean(cluster)))
            cluster = [lv]
    clustered.append(float(np.mean(cluster)))

    clustered.sort(key=lambda x: abs(x - price))
    levels = [round(l, 2) for l in clustered[:n_levels]]

    above = sum(1 for l in levels if l > price)
    below = sum(1 for l in levels if l < price)
    if   below > above: bias = "BULLISH"
    elif above > below: bias = "BEARISH"
    else:               bias = "NEUTRAL"

    return levels, bias


def _compute_gex_from_options(options: List[dict],
                               spot: float
                               ) -> Tuple[float, float, str, float, float, float]:
    """
    Gamma Exposure (GEX) from options chain.

    Formula per strike:
        GEX = gamma * open_interest * 100 * spot^2 * 0.01
        Calls add positive GEX (dealers short calls = short gamma)
        Puts  add negative GEX (dealers short puts  = long gamma)

    Positive total GEX  → dealers are net long gamma → they sell rallies / buy dips (pin effect)
    Negative total GEX  → dealers are net short gamma → they chase moves (amplify volatility)
    """
    if not options:
        return 0.0, spot, "NEUTRAL", spot, spot, spot

    strike_gex  = {}
    call_gamma  = {}
    put_gamma   = {}
    pain_values = {}

    for opt in options:
        try:
            strike   = float(opt.get("strike") or 0)
            oi       = int(opt.get("open_interest") or 0)
            greeks   = opt.get("greeks") or {}
            gamma    = float(greeks.get("gamma") or 0)
            opt_type = str(opt.get("option_type") or "").lower()
            bid      = float(opt.get("bid") or 0)
            ask      = float(opt.get("ask") or 0)
            mid      = (bid + ask) / 2
            if strike <= 0 or gamma == 0:
                continue
            gex_c = gamma * oi * 100 * (spot ** 2) * 0.01
            if opt_type == "call":
                strike_gex[strike] = strike_gex.get(strike, 0) + gex_c
                call_gamma[strike] = call_gamma.get(strike, 0) + gamma * oi
            elif opt_type == "put":
                strike_gex[strike] = strike_gex.get(strike, 0) - gex_c
                put_gamma[strike]  = put_gamma.get(strike, 0)  + gamma * oi
            pain_values[strike] = pain_values.get(strike, 0) + mid * oi * 100
        except Exception:
            continue

    if not strike_gex:
        return 0.0, spot, "NEUTRAL", spot, spot, spot

    total_gex = sum(strike_gex.values())

    # GEX flip level (sign change walking up strikes)
    sorted_strikes = sorted(strike_gex.keys())
    cumulative = 0.0
    flip_level = spot
    for s in sorted_strikes:
        prev       = cumulative
        cumulative += strike_gex[s]
        if prev != 0 and prev * cumulative < 0:
            flip_level = s
            break

    call_wall = max(call_gamma, key=call_gamma.get) if call_gamma else spot
    put_wall  = max(put_gamma,  key=put_gamma.get)  if put_gamma  else spot
    max_pain  = min(pain_values, key=pain_values.get) if pain_values else spot

    if   total_gex > 0:  gex_bias = "LONG"
    elif total_gex < 0:  gex_bias = "SHORT"
    else:                gex_bias = "NEUTRAL"

    return (round(total_gex, 0), round(flip_level, 2), gex_bias,
            round(call_wall, 2), round(put_wall, 2), round(max_pain, 2))


def _detect_patterns(opens: np.ndarray, highs: np.ndarray,
                     lows: np.ndarray, closes: np.ndarray,
                     ema9: np.ndarray, ema21: np.ndarray,
                     volumes: np.ndarray) -> List[str]:
    patterns = []
    n = len(closes)
    if n < 30:
        return patterns
    c, h, l, v = closes, highs, lows, volumes

    # EMA crossover
    if ema9[-1] > ema21[-1] and ema9[-2] <= ema21[-2]:
        patterns.append("EMA9 x EMA21 Bullish Cross ✅")
    elif ema9[-1] < ema21[-1] and ema9[-2] >= ema21[-2]:
        patterns.append("EMA9 x EMA21 Bearish Cross ❌")

    # Double bottom
    window     = min(30, n)
    local_lows = []
    for i in range(2, window - 2):
        idx = n - window + i
        if l[idx] < l[idx-1] and l[idx] < l[idx-2] and l[idx] < l[idx+1] and l[idx] < l[idx+2]:
            local_lows.append(idx)
    if len(local_lows) >= 2:
        v1, v2 = local_lows[-2], local_lows[-1]
        if abs(l[v1] - l[v2]) / max(l[v1], 0.01) < 0.03 and c[-1] > c[v1:v2].max():
            patterns.append("Double Bottom 🟢 (Bullish Reversal)")

    # Double top
    local_highs = []
    for i in range(2, window - 2):
        idx = n - window + i
        if h[idx] > h[idx-1] and h[idx] > h[idx-2] and h[idx] > h[idx+1] and h[idx] > h[idx+2]:
            local_highs.append(idx)
    if len(local_highs) >= 2:
        p1, p2 = local_highs[-2], local_highs[-1]
        if abs(h[p1] - h[p2]) / max(h[p1], 0.01) < 0.03 and c[-1] < c[p1:p2].min():
            patterns.append("Double Top 🔴 (Bearish Reversal)")

    # Bull / Bear flag
    if n >= 15:
        pole_start = n - 15
        pole_gain  = (c[n-10] - c[pole_start]) / max(c[pole_start], 0.01)
        cons_range = (h[n-10:n].max() - l[n-10:n].min()) / max(c[n-10], 0.01)
        if pole_gain > 0.05 and cons_range < 0.04:
            patterns.append("Bull Flag 🐂 (Continuation)")
        pole_drop = (c[pole_start] - c[n-10]) / max(c[pole_start], 0.01)
        if pole_drop > 0.05 and cons_range < 0.04:
            patterns.append("Bear Flag 🐻 (Continuation)")

    # Breakout / Breakdown
    if n >= 33:
        res_level = h[n-30:n-3].max()
        if c[-1] > res_level and c[-2] <= res_level:
            patterns.append("Breakout Above Resistance 🚀")
        sup_level = l[n-30:n-3].min()
        if c[-1] < sup_level and c[-2] >= sup_level:
            patterns.append("Breakdown Below Support 💥")

    # Inside bar
    if h[-1] < h[-2] and l[-1] > l[-2]:
        patterns.append("Inside Bar (Compression Setup)")

    # Pennant
    if n >= 20:
        rh = h[-10:]
        rl = l[-10:]
        if rh[-1] < rh[0] and rl[-1] > rl[0]:
            move = abs(c[-10] - c[-20]) / max(c[-20], 0.01)
            if move > 0.04:
                patterns.append("Pennant (Breakout Pending)")

    # Simple Head & Shoulders
    if n >= 25:
        seg   = h[n-25:]
        peaks = [(i, seg[i]) for i in range(1, len(seg) - 1)
                 if seg[i] > seg[i-1] and seg[i] > seg[i+1]]
        if len(peaks) >= 3:
            left, head, right = peaks[-3], peaks[-2], peaks[-1]
            if (head[1] > left[1] * 1.02 and head[1] > right[1] * 1.02
                    and abs(left[1] - right[1]) / max(head[1], 0.01) < 0.05):
                patterns.append("Head & Shoulders 📉 (Bearish)")

    # Volume climax / exhaustion
    if len(v) >= 20:
        avg_vol = v[-20:].mean()
        if v[-1] > avg_vol * 3 and abs(c[-1] - c[-2]) / max(c[-2], 0.01) < 0.005:
            patterns.append("Volume Climax (Exhaustion Warning ⚠️)")

    return patterns


# ── Main public function ───────────────────────────────────────────────────────

def compute_signals(bars: List[dict],
                    options: List[dict] = None) -> Optional[TechnicalSignals]:
    """
    bars   : list of OHLCV dicts.
             Polygon keys: o / h / l / c / v
             Tradier keys: open / high / low / close / volume
    options: optional full options chain — used for GEX computation.
    """
    if not bars or len(bars) < 30:
        return None

    def _f(b, *keys):
        for k in keys:
            if k in b:
                try:
                    return float(b[k])
                except Exception:
                    pass
        return 0.0

    opens   = np.array([_f(b, "o", "open")   for b in bars])
    highs   = np.array([_f(b, "h", "high")   for b in bars])
    lows    = np.array([_f(b, "l", "low")    for b in bars])
    closes  = np.array([_f(b, "c", "close")  for b in bars])
    volumes = np.array([_f(b, "v", "volume") for b in bars])
    spot    = closes[-1]

    # ── Trend ──────────────────────────────────────────────────────────────
    ema9_a   = _ema(closes, 9)
    ema21_a  = _ema(closes, 21)
    ema50_a  = _ema(closes, 50)
    ema200_a = _ema(closes, 200) if len(closes) >= 200 else np.full_like(closes, closes.mean())
    sma20_a  = _sma(closes, 20)
    sma200_a = _sma(closes, 200)
    vwap_a   = _vwap(highs, lows, closes, volumes)

    # ── Momentum ───────────────────────────────────────────────────────────
    rsi_a              = _rsi(closes, 14)
    stoch_k_a, stoch_d_a = _stochastic(highs, lows, closes)
    wr_a               = _williams_r(highs, lows, closes, 14)
    macd_line          = _ema(closes, 12) - _ema(closes, 26)
    signal_line        = _ema(macd_line, 9)
    macd_hist_a        = macd_line - signal_line
    cmf_a              = _cmf(highs, lows, closes, volumes, 20)

    # ── Volatility ─────────────────────────────────────────────────────────
    atr_a              = _atr(highs, lows, closes, 14)
    bb_u, bb_m, bb_l   = _bollinger(closes, 20, 2.0)
    hv20               = _hist_vol(closes, 20)

    def _safe(arr):
        v = float(arr[-1])
        return v if not math.isnan(v) else 0.0

    atr_val    = _safe(atr_a)
    bb_u_v     = _safe(bb_u)
    bb_l_v     = _safe(bb_l)
    bb_m_v     = _safe(bb_m)
    bb_width_v = (bb_u_v - bb_l_v) / max(bb_m_v, 0.01)

    # ── Volume / Trend strength ────────────────────────────────────────────
    obv_a              = _obv(closes, volumes)
    obv_ema_a          = _ema(obv_a, 20)
    adx_a, pdi_a, mdi_a = _adx(highs, lows, closes, 14)

    # ── Levels ─────────────────────────────────────────────────────────────
    sup, res = _find_support_resistance(highs, lows, closes)
    sup = min(sup, spot)
    res = max(res, spot)
    
    prev_h, prev_l, prev_c = float(highs[-2]), float(lows[-2]), float(closes[-2])
    piv, r1, r2, r3, s1, s2, s3 = _pivot_points(prev_h, prev_l, prev_c)
    cr3, cr4, cs3, cs4          = _camarilla_pivots(prev_h, prev_l, prev_c)

    # ── Dark Pool ──────────────────────────────────────────────────────────
    dp_levels, dp_bias = _detect_dark_pool_levels(closes, volumes, spot)

    # ── GEX ────────────────────────────────────────────────────────────────
    gex_total = gex_flip = 0.0
    gex_bias_str = "NEUTRAL"
    call_wall = put_wall = max_pain = spot
    if options:
        gex_total, gex_flip, gex_bias_str, call_wall, put_wall, max_pain = \
            _compute_gex_from_options(options, spot)

    # ── Patterns ───────────────────────────────────────────────────────────
    patterns = _detect_patterns(opens, highs, lows, closes, ema9_a, ema21_a, volumes)

    # ── Bias scoring ───────────────────────────────────────────────────────
    score = 0.0

    if ema9_a[-1]  > ema21_a[-1]:   score += 0.12
    else:                            score -= 0.12
    if ema21_a[-1] > ema50_a[-1]:   score += 0.08
    else:                            score -= 0.08
    if spot        > ema50_a[-1]:   score += 0.08
    else:                            score -= 0.08
    if spot        > ema200_a[-1]:  score += 0.06
    else:                            score -= 0.06

    rsi_val = float(rsi_a[-1])
    if   rsi_val < 35:  score += 0.15
    elif rsi_val > 65:  score -= 0.15
    elif rsi_val < 45:  score += 0.05
    elif rsi_val > 55:  score -= 0.05

    if   stoch_k_a[-1] < 20:  score += 0.08
    elif stoch_k_a[-1] > 80:  score -= 0.08

    if   wr_a[-1] < -80:  score += 0.06
    elif wr_a[-1] > -20:  score -= 0.06

    if   macd_hist_a[-1] > 0 and macd_hist_a[-2] <= 0:  score += 0.15
    elif macd_hist_a[-1] < 0 and macd_hist_a[-2] >= 0:  score -= 0.15
    elif macd_hist_a[-1] > 0:  score += 0.07
    elif macd_hist_a[-1] < 0:  score -= 0.07

    cmf_val = float(cmf_a[-1])
    if   cmf_val >  0.1:  score += 0.08
    elif cmf_val < -0.1:  score -= 0.08

    adx_val = float(adx_a[-1])
    if adx_val > 25:
        if pdi_a[-1] > mdi_a[-1]:  score += 0.06
        else:                       score -= 0.06

    if obv_a[-1] > obv_ema_a[-1]:  score += 0.05
    else:                            score -= 0.05

    if spot > vwap_a[-1]:  score += 0.05
    else:                   score -= 0.05

    if   gex_bias_str == "SHORT":  score += 0.05
    elif gex_bias_str == "LONG":   score -= 0.03

    if   dp_bias == "BULLISH":  score += 0.06
    elif dp_bias == "BEARISH":  score -= 0.06

    for p in patterns:
        if any(x in p for x in ["Bullish", "Bull", "Breakout", "Bottom", "✅"]):
            score += 0.10
        if any(x in p for x in ["Bearish", "Bear", "Breakdown", "Top", "❌", "📉"]):
            score -= 0.10

    score = round(max(-1.0, min(1.0, score)), 3)

    if   score >  0.10:  bias = "BULLISH"
    elif score < -0.10:  bias = "BEARISH"
    else:                bias = "NEUTRAL"

    if   adx_val > 30:  trend_str = "STRONG"
    elif adx_val > 20:  trend_str = "MODERATE"
    else:               trend_str = "WEAK"

    return TechnicalSignals(
        ema9=round(float(ema9_a[-1]),   2),
        ema21=round(float(ema21_a[-1]), 2),
        ema50=round(float(ema50_a[-1]), 2),
        ema200=round(float(ema200_a[-1]), 2),
        sma20=round(_safe(sma20_a),   2),
        sma200=round(_safe(sma200_a), 2),
        vwap=round(float(vwap_a[-1]), 2),
        rsi=round(rsi_val, 1),
        stoch_k=round(float(stoch_k_a[-1]), 1),
        stoch_d=round(float(stoch_d_a[-1]), 1),
        prev_stoch_k=round(float(stoch_k_a[-2]), 1),
        prev_stoch_d=round(float(stoch_d_a[-2]), 1),
        williams_r=round(float(wr_a[-1]),   1),
        prev_williams_r=round(float(wr_a[-2]), 1),
        macd=round(float(macd_line[-1]),     4),
        macd_signal=round(float(signal_line[-1]), 4),
        macd_hist=round(float(macd_hist_a[-1]),   4),
        prev_macd_hist=round(float(macd_hist_a[-2]), 4),
        cmf=round(cmf_val, 3),
        atr=round(atr_val, 2),
        atr_pct=round(atr_val / max(spot, 0.01) * 100, 2),
        bb_upper=round(bb_u_v, 2),
        bb_lower=round(bb_l_v, 2),
        bb_mid=round(bb_m_v,   2),
        bb_width=round(bb_width_v, 3),
        hist_vol_20=round(hv20, 3),
        obv=round(float(obv_a[-1]),      0),
        obv_ema=round(float(obv_ema_a[-1]), 0),
        adx=round(adx_val, 1),
        plus_di=round(float(pdi_a[-1]),  1),
        minus_di=round(float(mdi_a[-1]), 1),
        support=round(sup, 2),
        resistance=round(res, 2),
        pivot=round(piv, 2),
        r1=round(r1, 2), r2=round(r2, 2), r3=round(r3, 2),
        s1=round(s1, 2), s2=round(s2, 2), s3=round(s3, 2),
        cam_r3=round(cr3, 2), cam_r4=round(cr4, 2),
        cam_s3=round(cs3, 2), cam_s4=round(cs4, 2),
        gex=gex_total,
        gex_flip=gex_flip,
        gex_bias=gex_bias_str,
        call_wall=call_wall,
        put_wall=put_wall,
        max_pain=max_pain,
        dark_pool_levels=dp_levels,
        dark_pool_bias=dp_bias,
        patterns=patterns,
        bias=bias,
        bias_score=score,
        trend_strength=trend_str,
    )
