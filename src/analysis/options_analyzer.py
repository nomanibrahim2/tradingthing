"""
src/analysis/options_analyzer.py
Options chain analysis: strike selection, Greeks evaluation,
IV vs HV comparison, GEX-aware confidence scoring, callout generation.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional, Tuple

from .technicals import TechnicalSignals
from .flow_classifier import classify_flow, FlowIntelligence
from .greeks import compute_bs_price

log = logging.getLogger("OptionsAnalyzer")


@dataclass
class OptionCallout:
    # ── Identity ───────────────────────────────────────────────────────────
    symbol:       str
    option_type:  str       # 'call' | 'put'
    strike:       float
    expiration:   str       # YYYY-MM-DD
    dte:          int       # days to expiration

    # ── Pricing ────────────────────────────────────────────────────────────
    bid:              float
    ask:              float
    mid:              float
    entry_price:      float
    target_price:     float
    stop_loss:        float
    underlying_price: float

    # ── Greeks ─────────────────────────────────────────────────────────────
    delta:  float
    gamma:  float
    theta:  float
    vega:   float
    iv:     float           # implied vol as percentage (e.g. 45.2)

    # ── Liquidity ──────────────────────────────────────────────────────────
    open_interest:      int
    volume:             int
    bid_ask_spread:     float
    bid_ask_spread_pct: float

    # ── Risk ───────────────────────────────────────────────────────────────
    max_loss:       float
    reward_risk:    float
    prob_of_profit: float

    # ── Confidence ─────────────────────────────────────────────────────────
    confidence:       float
    confidence_tier:  str   # 'HIGH' | 'MEDIUM' | 'LOW'
    confidence_color: str   # 🟢 🟡 🔴

    # ── Strategy & Context ─────────────────────────────────────────────────
    strategy:   str
    trigger:    str
    patterns:   List[str] = field(default_factory=list)
    iv_vs_hv:   str       = ""

    # ── GEX fields ─────────────────────────────────────────────────────────
    gex:         float = 0.0
    gex_flip:    float = 0.0
    gex_bias:    str   = "NEUTRAL"
    call_wall:   float = 0.0
    put_wall:    float = 0.0
    max_pain:    float = 0.0

    # ── Dark Pool ──────────────────────────────────────────────────────────
    dark_pool_levels: List[float] = field(default_factory=list)
    dark_pool_bias:   str         = "NEUTRAL"

    # ── Extra technicals ───────────────────────────────────────────────────
    adx:          float = 0.0
    trend_strength: str = "WEAK"
    vwap:         float = 0.0
    stoch_k:      float = 50.0
    williams_r:   float = -50.0
    cmf:          float = 0.0
    obv_trend:    str   = "NEUTRAL"  # "RISING" | "FALLING" | "NEUTRAL"

    # ── Pivot levels ───────────────────────────────────────────────────────
    pivot:  float = 0.0
    r1:     float = 0.0
    s1:     float = 0.0
    cam_r3: float = 0.0
    cam_s3: float = 0.0

    notes: str = ""
    iv_corrected: bool  = False   # True if IV was re-derived from market price
    premium_inflated: bool = False  # True if premium >> BS theoretical price

    # ── Flow Intelligence (set by classifier) ──────────────────────────────
    trade_classification: str = ""      # BLOCK_TRADE, SWEEP, CHEAP_LOTTERY, etc.
    trade_intent:         str = ""      # DIRECTIONAL_BET, HEDGE, SPREAD_LEG, etc.
    conviction:           str = ""      # HIGH / MEDIUM / LOW
    conviction_score:     float = 0.0   # 0.0–1.0
    explanation:          str = ""      # human-readable narrative
    flow_pattern:         str = ""      # from FlowTracker
    intelligence_flags:   List[str] = field(default_factory=list)


class OptionsAnalyzer:
    def __init__(self, settings):
        self.s = settings

    # ── Directional callout ────────────────────────────────────────────────
    def analyze_chain(
        self,
        symbol: str,
        underlying_price: float,
        options: List[dict],
        signals: TechnicalSignals,
        avg_daily_volume: float = 0,
    ) -> List[OptionCallout]:
        if not options or signals.bias == "NEUTRAL":
            return []

        option_type = "call" if signals.bias == "BULLISH" else "put"
        candidates  = [o for o in options
                       if o.get("option_type", "").lower() == option_type]

        # ── Liquidity filter ───────────────────────────────────────────────
        liquid = []
        for opt in candidates:
            oi   = int(opt.get("open_interest") or 0)
            vol  = int(opt.get("volume") or 0)
            bid  = float(opt.get("bid") or 0)
            ask  = float(opt.get("ask") or 0)
            if oi < self.s.MIN_OPEN_INTEREST:  continue
            if vol < self.s.MIN_OPTION_VOLUME: continue
            mid  = (bid + ask) / 2
            if mid <= 0:                        continue
            spread     = ask - bid
            spread_pct = spread / mid
            if spread_pct > self.s.MAX_BID_ASK_SPREAD_PCT: continue
            liquid.append((opt, bid, ask, mid, spread, spread_pct))

        if not liquid:
            return []

        # ── Best strike selection (delta 0.25–0.65) ────────────────────────
        best       = None
        best_score = -999
        for (opt, bid, ask, mid, spread, spread_pct) in liquid:
            greeks = opt.get("greeks") or {}
            delta  = abs(float(greeks.get("delta") or 0))
            if not (0.25 <= delta <= 0.65):
                continue
            d_score   = 1 - abs(delta - 0.40) / 0.40
            liq_score = 1 - spread_pct / self.s.MAX_BID_ASK_SPREAD_PCT
            oi_score  = min(1.0, int(opt.get("open_interest") or 0) / 5000)
            total     = d_score * 0.4 + liq_score * 0.35 + oi_score * 0.25
            if total > best_score:
                best_score = total
                best = (opt, bid, ask, mid, spread, spread_pct)

        if not best:
            return []

        opt, bid, ask, mid, spread, spread_pct = best
        greeks = opt.get("greeks") or {}

        strike = float(opt.get("strike") or 0)
        exp    = str(opt.get("expiration_date") or "")
        dte    = self._dte(exp)
        iv     = float(greeks.get("mid_iv") or greeks.get("smv_vol") or 0)
        delta  = float(greeks.get("delta") or 0)
        gamma  = float(greeks.get("gamma") or 0)
        theta  = float(greeks.get("theta") or 0)
        vega   = float(greeks.get("vega") or 0)
        oi     = int(opt.get("open_interest") or 0)
        vol    = int(opt.get("volume") or 0)
        iv_corrected = bool(opt.get("iv_corrected", False))

        # ── Premium sanity check ──────────────────────────────────────────
        premium_inflated = False
        if iv > 0 and mid > 0 and dte > 0:
            bs_theo = compute_bs_price(underlying_price, strike, iv, dte,
                                       self.s.RISK_FREE_RATE, option_type)
            if bs_theo > 0 and mid > bs_theo * self.s.MAX_PREMIUM_MULTIPLE:
                premium_inflated = True

        # ── TP / SL ────────────────────────────────────────────────────────
        atr   = signals.atr
        entry = round(mid * 1.02, 2)

        if option_type == "call":
            tp_und = underlying_price + atr * 2.0
            sl_und = underlying_price - atr * 1.0
        else:
            tp_und = underlying_price - atr * 2.0
            sl_und = underlying_price + atr * 1.0

        tp = round(entry + abs(delta) * abs(tp_und - underlying_price), 2)
        sl = round(entry - abs(delta) * abs(sl_und - underlying_price), 2)
        sl = max(sl, entry * 0.40)

        reward_risk = round((tp - entry) / (entry - sl), 2) if entry > sl else 0.0
        pop         = round(abs(delta) * 0.85, 2)

        # ── IV vs HV ───────────────────────────────────────────────────────
        hv = signals.hist_vol_20
        if hv > 0 and iv > 0:
            ratio = iv / hv
            if   ratio < self.s.IV_HV_CHEAP_THRESHOLD:     iv_vs_hv = "CHEAP"
            elif ratio > self.s.IV_HV_EXPENSIVE_THRESHOLD: iv_vs_hv = "EXPENSIVE"
            else:                                           iv_vs_hv = "FAIR"
        else:
            iv_vs_hv = "UNKNOWN"

        # ── Confidence ─────────────────────────────────────────────────────
        confidence       = self._score_confidence(signals, reward_risk, pop,
                                                  spread_pct, iv_vs_hv, oi, vol,
                                                  premium_inflated=premium_inflated,
                                                  iv_corrected=iv_corrected)
        tier, color      = self._confidence_tier(confidence)

        if tier == "LOW":
            return []

        trigger = self._build_trigger(signals, option_type)
        obv_trend = ("RISING"  if signals.obv > signals.obv_ema else
                     "FALLING" if signals.obv < signals.obv_ema else "NEUTRAL")

        callout = OptionCallout(
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            expiration=exp,
            dte=dte,
            bid=bid,
            ask=ask,
            mid=mid,
            entry_price=entry,
            target_price=tp,
            stop_loss=sl,
            underlying_price=underlying_price,
            delta=round(delta, 3),
            gamma=round(gamma, 4),
            theta=round(theta, 4),
            vega=round(vega,  4),
            iv=round(iv * 100, 1),
            open_interest=oi,
            volume=vol,
            bid_ask_spread=round(spread, 2),
            bid_ask_spread_pct=round(spread_pct * 100, 1),
            max_loss=round(entry * 100, 2),
            reward_risk=reward_risk,
            prob_of_profit=pop,
            confidence=confidence,
            confidence_tier=tier,
            confidence_color=color,
            strategy="DIRECTIONAL",
            trigger=trigger,
            patterns=signals.patterns,
            iv_vs_hv=iv_vs_hv,
            # GEX
            gex=signals.gex,
            gex_flip=signals.gex_flip,
            gex_bias=signals.gex_bias,
            call_wall=signals.call_wall,
            put_wall=signals.put_wall,
            max_pain=signals.max_pain,
            # Dark Pool
            dark_pool_levels=signals.dark_pool_levels,
            dark_pool_bias=signals.dark_pool_bias,
            # Extra technicals
            adx=signals.adx,
            trend_strength=signals.trend_strength,
            vwap=signals.vwap,
            stoch_k=signals.stoch_k,
            williams_r=signals.williams_r,
            cmf=signals.cmf,
            obv_trend=obv_trend,
            # Pivots
            pivot=signals.pivot,
            r1=signals.r1,
            s1=signals.s1,
            cam_r3=signals.cam_r3,
            cam_s3=signals.cam_s3,
            # IV/premium quality
            iv_corrected=iv_corrected,
            premium_inflated=premium_inflated,
        )
        return [callout]

    # ── Unusual flow ──────────────────────────────────────────────────────
    def analyze_unusual_flow(
        self,
        symbol: str,
        underlying_price: float,
        options: List[dict],
        avg_daily_volume: float,
        signals: TechnicalSignals = None,
    ) -> List[OptionCallout]:
        alerts = []
        for opt in options:
            vol    = int(opt.get("volume") or 0)
            oi     = int(opt.get("open_interest") or 0)
            bid    = float(opt.get("bid") or 0)
            ask    = float(opt.get("ask") or 0)
            mid    = (bid + ask) / 2
            if mid <= 0: continue
            premium     = vol * mid * 100
            vol_oi_ratio = vol / max(oi, 1)

            is_unusual_vol   = vol_oi_ratio >= self.s.FLOW_VOLUME_MULTIPLIER
            is_large_premium = premium      >= self.s.FLOW_MIN_PREMIUM
            if not (is_unusual_vol or is_large_premium):
                continue

            greeks   = opt.get("greeks") or {}
            delta    = float(greeks.get("delta") or 0)
            theta    = float(greeks.get("theta") or 0)
            vega     = float(greeks.get("vega") or 0)
            gamma    = float(greeks.get("gamma") or 0)
            iv       = float(greeks.get("mid_iv") or greeks.get("smv_vol") or 0)
            strike   = float(opt.get("strike") or 0)
            exp      = str(opt.get("expiration_date") or "")
            dte      = self._dte(exp)
            opt_type = str(opt.get("option_type") or "").lower()
            iv_corrected = bool(opt.get("iv_corrected", False))

            # ── Premium inflation check ───────────────────────────────────
            premium_inflated = False
            if iv > 0 and mid > 0 and dte > 0:
                bs_theo = compute_bs_price(underlying_price, strike, iv, dte,
                                           self.s.RISK_FREE_RATE, opt_type)
                if bs_theo > 0 and mid > bs_theo * self.s.MAX_PREMIUM_MULTIPLE:
                    premium_inflated = True

            spread     = ask - bid
            spread_pct = spread / mid
            if spread_pct > 0.30: continue

            entry = mid
            tp    = round(entry * 2.0,  2)
            sl    = round(entry * 0.50, 2)
            rr    = round((tp - entry) / max(entry - sl, 0.01), 2)

            # ── Flow Intelligence ─────────────────────────────────────────
            hv20 = signals.hist_vol_20 if signals else 0.0
            intel = classify_flow(
                opt, underlying_price,
                signals=signals,
                all_options=options,
                hv20=hv20,
            )

            # Use classifier's conviction score instead of flat value
            confidence = intel.conviction_score
            tier, color = self._confidence_tier(confidence)

            trigger_parts = []
            if is_unusual_vol:   trigger_parts.append(f"Vol/OI {vol_oi_ratio:.1f}x")
            if is_large_premium: trigger_parts.append(f"${premium:,.0f} premium")

            obv_trend = "NEUTRAL"
            if signals:
                obv_trend = ("RISING"  if signals.obv > signals.obv_ema else
                             "FALLING" if signals.obv < signals.obv_ema else "NEUTRAL")

            callout = OptionCallout(
                symbol=symbol,
                option_type=opt_type,
                strike=strike,
                expiration=exp,
                dte=dte,
                bid=bid,
                ask=ask,
                mid=mid,
                entry_price=entry,
                target_price=tp,
                stop_loss=sl,
                underlying_price=underlying_price,
                delta=round(delta, 3),
                gamma=round(gamma, 4),
                theta=round(theta, 4),
                vega=round(vega,   4),
                iv=round(iv * 100, 1),
                open_interest=oi,
                volume=vol,
                bid_ask_spread=round(spread, 2),
                bid_ask_spread_pct=round(spread_pct * 100, 1),
                max_loss=round(entry * 100, 2),
                reward_risk=rr,
                prob_of_profit=round(abs(delta) * 0.80, 2),
                confidence=confidence,
                confidence_tier=tier,
                confidence_color=color,
                strategy="UNUSUAL_FLOW",
                trigger=" | ".join(trigger_parts),
                # GEX / DP from signals if available
                gex=signals.gex          if signals else 0.0,
                gex_flip=signals.gex_flip if signals else 0.0,
                gex_bias=signals.gex_bias if signals else "NEUTRAL",
                call_wall=signals.call_wall if signals else 0.0,
                put_wall=signals.put_wall   if signals else 0.0,
                max_pain=signals.max_pain   if signals else 0.0,
                dark_pool_levels=signals.dark_pool_levels if signals else [],
                dark_pool_bias=signals.dark_pool_bias     if signals else "NEUTRAL",
                adx=signals.adx if signals else 0.0,
                trend_strength=signals.trend_strength if signals else "WEAK",
                obv_trend=obv_trend,
                notes=f"Dollar premium: ${premium:,.0f}",
                # Flow Intelligence
                trade_classification=intel.classification,
                trade_intent=intel.intent,
                conviction=intel.conviction,
                conviction_score=intel.conviction_score,
                explanation=intel.explanation,
                intelligence_flags=intel.flags,
                # IV/premium quality
                iv_corrected=iv_corrected,
                premium_inflated=premium_inflated,
            )
            alerts.append(callout)

        alerts.sort(key=lambda x: x.volume * x.mid * 100, reverse=True)
        return alerts[:5]

    # ── Helpers ───────────────────────────────────────────────────────────
    def _dte(self, exp_str: str) -> int:
        try:
            return max(0, (datetime.strptime(exp_str, "%Y-%m-%d").date() - date.today()).days)
        except Exception:
            return 0

    def _score_confidence(self, signals: TechnicalSignals, reward_risk: float,
                          pop: float, spread_pct: float, iv_vs_hv: str,
                          oi: int, vol: int,
                          premium_inflated: bool = False,
                          iv_corrected: bool = False) -> float:
        score = 0.0

        # Core bias strength
        score += abs(signals.bias_score) * 0.20

        # RSI
        if signals.bias == "BULLISH" and signals.rsi < 40:    score += 0.08
        elif signals.bias == "BEARISH" and signals.rsi > 60:  score += 0.08

        # Stochastic
        if signals.bias == "BULLISH" and signals.stoch_k < 25: score += 0.05
        elif signals.bias == "BEARISH" and signals.stoch_k > 75: score += 0.05

        # MACD alignment
        if signals.bias == "BULLISH" and signals.macd_hist > 0:  score += 0.08
        elif signals.bias == "BEARISH" and signals.macd_hist < 0: score += 0.08

        # CMF confirms money flow direction
        if signals.bias == "BULLISH" and signals.cmf > 0.05:  score += 0.05
        elif signals.bias == "BEARISH" and signals.cmf < -0.05: score += 0.05

        # ADX trend strength
        if signals.adx > 30:   score += 0.10
        elif signals.adx > 20: score += 0.05

        # VWAP alignment
        if signals.bias == "BULLISH" and signals.vwap > 0:
            if signals.ema9 > signals.vwap: score += 0.04
        elif signals.bias == "BEARISH" and signals.vwap > 0:
            if signals.ema9 < signals.vwap: score += 0.04

        # GEX alignment
        if signals.bias == "BULLISH" and signals.gex_bias == "SHORT": score += 0.06
        elif signals.bias == "BEARISH" and signals.gex_bias == "SHORT": score += 0.04

        # Dark pool confirms
        if signals.dark_pool_bias == signals.bias[:len(signals.dark_pool_bias)]:
            score += 0.05

        # Pattern count
        score += min(0.15, len(signals.patterns) * 0.05)

        # R:R
        if   reward_risk >= 3.0: score += 0.12
        elif reward_risk >= 2.0: score += 0.08

        # IV/HV
        if   iv_vs_hv == "CHEAP":     score += 0.08
        elif iv_vs_hv == "EXPENSIVE": score -= 0.05

        # Liquidity
        if oi  > 5000:  score += 0.04
        if vol > 1000:  score += 0.04
        score -= spread_pct * 0.08

        # ── Premium / IV quality penalties ─────────────────────────────────
        if premium_inflated:
            score -= 0.15  # premium far exceeds theoretical — likely already moved
        if iv_corrected:
            score -= 0.03  # IV was re-derived — slight data quality concern

        return round(min(1.0, max(0.0, score)), 3)

    def _confidence_tier(self, confidence: float) -> Tuple[str, str]:
        if   confidence >= self.s.HIGH_CONF_THRESHOLD: return "HIGH",   "🟢"
        elif confidence >= self.s.MED_CONF_THRESHOLD:  return "MEDIUM", "🟡"
        else:                                           return "LOW",    "🔴"

    def _build_trigger(self, signals: TechnicalSignals, option_type: str) -> str:
        parts = []
        if signals.patterns:
            parts.append(signals.patterns[0])
        if abs(signals.bias_score) > 0.5:
            parts.append(f"Strong {'bullish' if option_type == 'call' else 'bearish'} momentum")
        if signals.rsi < 35:          parts.append("RSI oversold")
        elif signals.rsi > 65:        parts.append("RSI overbought")
        if signals.macd_hist > 0 and option_type == "call": parts.append("MACD bullish cross")
        elif signals.macd_hist < 0 and option_type == "put": parts.append("MACD bearish cross")
        if signals.adx > 25:          parts.append(f"ADX {signals.adx:.0f} (trend strength)")
        if signals.gex_bias == "SHORT": parts.append("Negative GEX (volatile conditions)")
        if signals.dark_pool_bias != "NEUTRAL":
            parts.append(f"Dark pool {signals.dark_pool_bias.lower()}")
        return " | ".join(parts) if parts else \
               f"{'Bullish' if option_type == 'call' else 'Bearish'} technical setup"
