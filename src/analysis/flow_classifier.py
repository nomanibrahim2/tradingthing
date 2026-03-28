"""
src/analysis/flow_classifier.py
Flow Intelligence Engine — classifies trades, detects intent,
and produces context-aware confidence scores with human-readable explanations.

Turns raw flow data into actionable intelligence:
  1. Trade Classification  — WHAT the trade is (block, lottery, sweep, etc.)
  2. Intent Detection       — WHY the trade was placed (directional, hedge, spread, MM)
  3. Conviction Scoring     — HOW much it matters (context-aware 0–1 score)
  4. Narrative Explanation   — plain-English summary for Discord embeds
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger("FlowClassifier")


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class FlowIntelligence:
    classification: str = "UNKNOWN"          # BLOCK_TRADE, SWEEP, CHEAP_LOTTERY, etc.
    intent:         str = "UNKNOWN"          # DIRECTIONAL_BET, HEDGE, SPREAD_LEG, etc.
    conviction:     str = "LOW"              # HIGH / MEDIUM / LOW
    conviction_score: float = 0.50
    explanation:    str = ""                 # human-readable narrative
    flow_pattern:   str = ""                 # from FlowTracker (set externally)
    flags:          List[str] = field(default_factory=list)  # warning flags


# ── Classification ────────────────────────────────────────────────────────────

_CLASSIFICATION_RULES = [
    # (name, test_fn) — evaluated in order, first match wins
]


def _classify_trade(opt: dict, underlying_price: float) -> Tuple[str, List[str]]:
    """
    Classify a single option contract based on structural features.
    Returns (classification, list_of_flags).
    """
    dte       = int(opt.get("dte") or 0)
    vol       = int(opt.get("volume") or 0)
    oi        = int(opt.get("open_interest") or 0)
    bid       = float(opt.get("bid") or 0)
    ask       = float(opt.get("ask") or 0)
    mid       = (bid + ask) / 2
    strike    = float(opt.get("strike") or 0)
    opt_type  = str(opt.get("option_type") or "").lower()
    greeks    = opt.get("greeks") or {}
    delta     = abs(float(greeks.get("delta") or 0))

    flags = []

    # ── Same-day expiry ───────────────────────────────────────────────────
    if dte == 0:
        flags.append("Same-day expiry — short-term activity, not high conviction")
        return "SAME_DAY_EXPIRY", flags

    # ── Post-dump inflated premium ────────────────────────────────────────
    # If IV is extreme (>200%) AND the contract has high premium, this is
    # likely aftermath of a big move, not a new opportunity
    iv = float(greeks.get("mid_iv") or greeks.get("smv_vol") or 0)
    premium = vol * mid * 100
    if iv > 2.0 and premium > 50_000:
        flags.append(f"⚠️ IV at {iv*100:.0f}% — premium inflated, underlying likely already moved")
        return "POST_DUMP_INFLATED", flags

    # ── Cheap lottery tickets ─────────────────────────────────────────────
    if mid < 0.30 and vol > 5000:
        flags.append("Cheap contracts + huge volume — likely fast trading, not strong positioning")
        return "CHEAP_LOTTERY", flags

    # ── Deep in-the-money ─────────────────────────────────────────────────
    if delta >= 0.85:
        flags.append("Deep ITM — behaves like stock, not a real options bet")
        return "DEEP_ITM", flags

    # ── Brand-new position ────────────────────────────────────────────────
    if oi == 0:
        flags.append("OI = 0 — new position, direction unclear until follow-through")
        return "NEW_POSITION", flags

    # ── Block trade ───────────────────────────────────────────────────────
    if vol >= 1000 and mid >= 1.00:
        flags.append("Block-sized order — institutional conviction")
        return "BLOCK_TRADE", flags

    # ── Sweep (approximated: very high vol relative to OI) ────────────────
    if oi > 0 and vol >= oi * 5 and vol >= 500:
        flags.append("Aggressive sweep — urgency signal, filled across exchanges")
        return "SWEEP", flags

    # ── Weekly short-term ─────────────────────────────────────────────────
    if dte <= 5:
        flags.append(f"Short-term ({dte}d expiry) — tactical trade, lower conviction")
        return "WEEKLY_SHORT_TERM", flags

    # ── Default ───────────────────────────────────────────────────────────
    return "STANDARD", flags


# ── Intent Detection ──────────────────────────────────────────────────────────

def _detect_intent(
    opt: dict,
    underlying_price: float,
    signals,            # TechnicalSignals or None
    all_options: List[dict],
) -> str:
    """
    Infer the likely intent behind a trade using structural + market context.
    """
    opt_type  = str(opt.get("option_type") or "").lower()
    strike    = float(opt.get("strike") or 0)
    vol       = int(opt.get("volume") or 0)
    oi        = int(opt.get("open_interest") or 0)
    exp       = str(opt.get("expiration_date") or "")
    greeks    = opt.get("greeks") or {}
    delta     = float(greeks.get("delta") or 0)

    # ── Market-maker signature: near-equal call + put vol at same strike ──
    paired_vol = 0
    opposite_type = "put" if opt_type == "call" else "call"
    for other in all_options:
        if (other.get("strike") == strike
                and other.get("expiration_date") == exp
                and str(other.get("option_type", "")).lower() == opposite_type):
            paired_vol = int(other.get("volume") or 0)
            break

    if paired_vol > 0 and vol > 0:
        ratio = min(vol, paired_vol) / max(vol, paired_vol)
        if ratio > 0.70:
            return "MARKET_MAKER"

    # ── Spread-leg: significant activity at nearby strikes same exp ───────
    nearby_activity = 0
    for other in all_options:
        if other is opt:
            continue
        if (other.get("expiration_date") == exp
                and abs(float(other.get("strike") or 0) - strike) / max(strike, 1) < 0.05
                and int(other.get("volume") or 0) > vol * 0.3):
            nearby_activity += 1
    if nearby_activity >= 1:
        return "SPREAD_LEG"

    # ── Hedge: flow direction opposes underlying trend ────────────────────
    if signals:
        is_bullish_flow = opt_type == "call"
        underlying_bearish = signals.bias == "BEARISH"
        underlying_bullish = signals.bias == "BULLISH"
        if (is_bullish_flow and underlying_bearish) or (not is_bullish_flow and underlying_bullish):
            return "HEDGE"

    # ── Position adjustment: high existing OI suggests adding to position ──
    if oi > 5000 and vol < oi * 0.5:
        return "POSITION_ADJUSTMENT"

    # ── Default: directional bet ──────────────────────────────────────────
    return "DIRECTIONAL_BET"


# ── Context-Aware Conviction Scoring ──────────────────────────────────────────

def _score_conviction(
    opt: dict,
    classification: str,
    intent: str,
    underlying_price: float,
    signals,            # TechnicalSignals or None
    iv_hv_ratio: float,
) -> Tuple[float, str, List[str]]:
    """
    Compute conviction score (0–1) based on trade structure + market context.
    Returns (score, tier, reasoning_parts).
    """
    score = 0.55   # baseline
    reasons = []

    dte       = int(opt.get("dte") or 0)
    vol       = int(opt.get("volume") or 0)
    oi        = int(opt.get("open_interest") or 0)
    bid       = float(opt.get("bid") or 0)
    ask       = float(opt.get("ask") or 0)
    mid       = (bid + ask) / 2
    strike    = float(opt.get("strike") or 0)
    opt_type  = str(opt.get("option_type") or "").lower()
    greeks    = opt.get("greeks") or {}
    delta     = abs(float(greeks.get("delta") or 0))
    premium   = vol * mid * 100

    # ── Post-dump / inflated premium penalty ──────────────────────────────
    if classification == "POST_DUMP_INFLATED":
        score -= 0.25
        reasons.append("Premium inflated post-move — underlying already dumped")

    # ── Time structure ────────────────────────────────────────────────────
    if dte == 0:
        score -= 0.25
        reasons.append("Same-day expiry (−conviction)")
    elif dte <= 5:
        score -= 0.10
        reasons.append(f"Short-term {dte}d expiry")
    elif 14 <= dte <= 60:
        score += 0.10
        reasons.append(f"Swing timeframe ({dte}d)")
    elif dte > 60:
        score += 0.05
        reasons.append(f"Longer-dated ({dte}d)")

    # ── Contract quality ──────────────────────────────────────────────────
    if mid < 0.30:
        score -= 0.15
        reasons.append("Cheap contract (<$0.30)")
    if delta >= 0.85:
        score -= 0.10
        reasons.append("Deep ITM (stock substitute)")
    if oi == 0:
        score -= 0.10
        reasons.append("No prior OI — new position")

    # ── Size / conviction signals ─────────────────────────────────────────
    if classification == "BLOCK_TRADE":
        score += 0.15
        reasons.append("Block-sized order (+conviction)")
    if classification == "SWEEP":
        score += 0.12
        reasons.append("Aggressive sweep (+conviction)")
    if premium >= 1_000_000:
        score += 0.10
        reasons.append(f"${premium/1e6:.1f}M premium — institutional size")
    elif premium >= 500_000:
        score += 0.05
        reasons.append(f"${premium/1e3:.0f}K premium")

    # ── Market context (requires signals) ─────────────────────────────────
    if signals:
        is_call = opt_type == "call"

        # Trend alignment
        if signals.adx > 25:
            trend_aligns = (
                (is_call and signals.bias == "BULLISH") or
                (not is_call and signals.bias == "BEARISH")
            )
            if trend_aligns:
                score += 0.15
                reasons.append(f"Aligns with {signals.bias.lower()} trend (ADX {signals.adx:.0f})")
            else:
                score -= 0.08
                reasons.append(f"Against prevailing trend (ADX {signals.adx:.0f})")

        # Flow into resistance (call) or support (put) = weaker
        if is_call and signals.resistance > 0:
            pct_to_resistance = (signals.resistance - underlying_price) / max(underlying_price, 1)
            if pct_to_resistance < 0.02:
                score -= 0.10
                reasons.append(f"Call flow into resistance (${signals.resistance:.2f})")
        if not is_call and signals.support > 0:
            pct_to_support = (underlying_price - signals.support) / max(underlying_price, 1)
            if pct_to_support < 0.02:
                score -= 0.10
                reasons.append(f"Put flow into support (${signals.support:.2f})")

        # GEX alignment
        if signals.gex_bias == "SHORT":
            score += 0.10
            reasons.append("Negative GEX — volatility amplified")
        elif signals.gex_bias == "LONG":
            score -= 0.05
            reasons.append("Positive GEX — dealer pinning likely")

        # VWAP alignment
        if is_call and underlying_price > signals.vwap:
            score += 0.03
            reasons.append("Price above VWAP")
        elif not is_call and underlying_price < signals.vwap:
            score += 0.03
            reasons.append("Price below VWAP")

    # ── IV context ────────────────────────────────────────────────────────
    if iv_hv_ratio > 0:
        if iv_hv_ratio < 0.90:
            score += 0.08
            reasons.append("IV cheap vs HV — options underpriced")
        elif iv_hv_ratio > 1.30:
            score -= 0.08
            reasons.append("IV expensive vs HV — options overpriced")

    # ── Intent modifiers ──────────────────────────────────────────────────
    if intent == "MARKET_MAKER":
        score -= 0.15
        reasons.append("Market-maker activity — not directional")
    if intent == "HEDGE":
        score -= 0.05
        reasons.append("Likely hedge — not a directional bet")
    if intent == "SPREAD_LEG":
        score -= 0.05
        reasons.append("Spread leg — part of multi-leg strategy")

    # ── Clamp and tier ────────────────────────────────────────────────────
    score = round(max(0.0, min(1.0, score)), 3)

    if score >= 0.70:
        tier = "HIGH"
    elif score >= 0.50:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    return score, tier, reasons


# ── Narrative Builder ─────────────────────────────────────────────────────────

_CLASSIFICATION_LABELS = {
    "SAME_DAY_EXPIRY":    "⏱️ Same-day expiry",
    "WEEKLY_SHORT_TERM":  "📅 Weekly",
    "CHEAP_LOTTERY":      "🎰 Lottery ticket",
    "DEEP_ITM":           "📦 Deep ITM (stock substitute)",
    "NEW_POSITION":       "🆕 New position (OI=0)",
    "BLOCK_TRADE":        "🐋 Block trade",
    "SWEEP":              "🌊 Aggressive sweep",
    "POST_DUMP_INFLATED": "📛 Post-move inflated premium",
    "STANDARD":           "📋 Standard flow",
    "UNKNOWN":            "❓ Unclassified",
}

_INTENT_LABELS = {
    "DIRECTIONAL_BET":     "🎯 Directional bet",
    "HEDGE":               "🛡️ Hedge",
    "POSITION_ADJUSTMENT": "🔧 Position adjustment",
    "SPREAD_LEG":          "📐 Multi-leg / spread",
    "MARKET_MAKER":        "🏦 Market-maker activity",
    "UNKNOWN":             "❓ Unknown",
}


def _build_explanation(
    opt: dict,
    classification: str,
    intent: str,
    conviction_tier: str,
    reasons: List[str],
    underlying_price: float,
) -> str:
    """Build a concise human-readable explanation for the embed."""
    strike   = float(opt.get("strike") or 0)
    opt_type = str(opt.get("option_type") or "").lower().upper()
    dte      = int(opt.get("dte") or 0)
    vol      = int(opt.get("volume") or 0)
    bid      = float(opt.get("bid") or 0)
    ask      = float(opt.get("ask") or 0)
    mid      = (bid + ask) / 2
    premium  = vol * mid * 100
    symbol   = str(opt.get("underlying") or "")

    cls_label = _CLASSIFICATION_LABELS.get(classification, classification)
    int_label = _INTENT_LABELS.get(intent, intent)

    parts = [f"{cls_label} — {vol:,} {symbol} ${strike:.0f} {opt_type}s"]

    if dte > 0:
        parts.append(f"expiring in {dte}d")
    else:
        parts.append("expiring today")

    if premium >= 1_000_000:
        parts.append(f"(${premium/1e6:.1f}M premium)")
    elif premium >= 1_000:
        parts.append(f"(${premium/1e3:.0f}K premium)")

    narrative = " ".join(parts) + "."

    # Add top 3 reasons
    if reasons:
        top_reasons = reasons[:3]
        narrative += " " + ". ".join(top_reasons) + "."

    narrative += f" Conviction: **{conviction_tier}**."
    return narrative


# ── Public API ────────────────────────────────────────────────────────────────

def classify_flow(
    opt: dict,
    underlying_price: float,
    signals=None,               # TechnicalSignals
    all_options: List[dict] = None,
    hv20: float = 0.0,
) -> FlowIntelligence:
    """
    Full flow intelligence pipeline for a single option contract.
    Returns a FlowIntelligence object with classification, intent,
    conviction, and a human-readable explanation.
    """
    all_options = all_options or []

    # 1. Classify
    classification, flags = _classify_trade(opt, underlying_price)

    # 2. Detect intent
    intent = _detect_intent(opt, underlying_price, signals, all_options)

    # 3. IV / HV ratio
    greeks = opt.get("greeks") or {}
    iv = float(greeks.get("mid_iv") or greeks.get("smv_vol") or 0)
    iv_hv_ratio = iv / hv20 if hv20 > 0 and iv > 0 else 0.0

    # 4. Score conviction
    score, tier, reasons = _score_conviction(
        opt, classification, intent, underlying_price, signals, iv_hv_ratio
    )

    # 5. Build narrative
    explanation = _build_explanation(
        opt, classification, intent, tier, reasons, underlying_price
    )

    return FlowIntelligence(
        classification=classification,
        intent=intent,
        conviction=tier,
        conviction_score=score,
        explanation=explanation,
        flags=flags,
    )
