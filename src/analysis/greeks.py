"""
src/analysis/greeks.py
Local Black-Scholes Greeks calculator with IV validation.
Computes Delta, Gamma, Theta, Vega from spot, strike, IV, DTE, risk-free rate.
Includes Newton-Raphson IV solver to re-derive IV when yfinance values are suspect.
"""

import math
from typing import Dict, Optional, Tuple

try:
    from scipy.stats import norm
except ImportError:
    norm = None

import logging

log = logging.getLogger("Greeks")

# ── IV bounds ────────────────────────────────────────────────────────────────
MIN_IV = 0.01    # 1%  — nothing trades below this
MAX_IV = 5.0     # 500% — even meme stocks rarely exceed this
IV_SOLVER_MAX_ITER = 50
IV_SOLVER_TOL = 1e-6


def _norm_cdf(x: float) -> float:
    """Standard normal CDF — fallback if scipy unavailable."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF — fallback if scipy unavailable."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _cdf(x: float) -> float:
    if norm is not None:
        return float(norm.cdf(x))
    return _norm_cdf(x)


def _pdf(x: float) -> float:
    if norm is not None:
        return float(norm.pdf(x))
    return _norm_pdf(x)


def _clamp_iv(iv: float) -> float:
    """Clamp IV to sane bounds."""
    if iv <= 0:
        return 0.0     # signal: invalid — let caller handle
    return max(MIN_IV, min(MAX_IV, iv))


# ── Black-Scholes theoretical price ─────────────────────────────────────────

def compute_bs_price(
    spot: float,
    strike: float,
    iv: float,
    dte: int,
    risk_free_rate: float = 0.045,
    option_type: str = "call",
) -> float:
    """
    Black-Scholes theoretical option price.
    Returns 0.0 if inputs are invalid.
    """
    if spot <= 0 or strike <= 0 or iv <= 0 or dte <= 0:
        return 0.0

    T = dte / 365.0
    sqrt_T = math.sqrt(T)
    sigma = iv

    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if option_type.lower() == "call":
        price = spot * _cdf(d1) - strike * math.exp(-risk_free_rate * T) * _cdf(d2)
    else:
        price = strike * math.exp(-risk_free_rate * T) * _cdf(-d2) - spot * _cdf(-d1)

    return max(0.0, price)


# ── Newton-Raphson IV solver ─────────────────────────────────────────────────

def compute_iv_from_price(
    market_price: float,
    spot: float,
    strike: float,
    dte: int,
    risk_free_rate: float = 0.045,
    option_type: str = "call",
) -> float:
    """
    Derive implied volatility from the option's market price using Bisection search.
    More robust than Newton-Raphson for deep OTM options where Vega approaches 0.
    Returns 0.0 if it fails to converge or inputs are invalid.
    """
    if market_price <= 0 or spot <= 0 or strike <= 0 or dte <= 0:
        return 0.0

    T = dte / 365.0

    # Intrinsic value check
    if option_type.lower() == "call":
        intrinsic = max(0, spot - strike * math.exp(-risk_free_rate * T))
    else:
        intrinsic = max(0, strike * math.exp(-risk_free_rate * T) - spot)

    if market_price < intrinsic - 0.01:
        return 0.0

    low_iv = MIN_IV
    high_iv = MAX_IV
    
    # Check bounds
    low_price = compute_bs_price(spot, strike, low_iv, dte, risk_free_rate, option_type)
    if market_price <= low_price:
        return low_iv
        
    high_price = compute_bs_price(spot, strike, high_iv, dte, risk_free_rate, option_type)
    if market_price >= high_price:
        return high_iv

    # Bisection search
    for _ in range(IV_SOLVER_MAX_ITER):
        mid_iv = (low_iv + high_iv) / 2.0
        mid_price = compute_bs_price(spot, strike, mid_iv, dte, risk_free_rate, option_type)
        
        diff = mid_price - market_price
        
        if abs(diff) < IV_SOLVER_TOL:
            return mid_iv
            
        if diff > 0:
            high_iv = mid_iv
        else:
            low_iv = mid_iv
            
    return (low_iv + high_iv) / 2.0


# ── Greeks ───────────────────────────────────────────────────────────────────

def compute_greeks(
    spot: float,
    strike: float,
    iv: float,
    dte: int,
    risk_free_rate: float = 0.045,
    option_type: str = "call",
) -> Dict[str, float]:
    """
    Compute Black-Scholes Greeks for a single option.

    Parameters
    ----------
    spot         : Current underlying price
    strike       : Option strike price
    iv           : Implied volatility as a DECIMAL (e.g. 0.35 for 35%)
    dte          : Days to expiration
    risk_free_rate: Annual risk-free rate (default 4.5%)
    option_type  : 'call' or 'put'

    Returns
    -------
    dict with keys: delta, gamma, theta, vega, rho
    """
    if spot <= 0 or strike <= 0 or dte <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    # Clamp IV to sane range
    iv = _clamp_iv(iv)
    if iv <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    T = dte / 365.0
    sqrt_T = math.sqrt(T)
    sigma = iv

    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    nd1 = _cdf(d1)
    nd2 = _cdf(d2)
    pd1 = _pdf(d1)

    is_call = option_type.lower() == "call"

    # Delta
    if is_call:
        delta = nd1
    else:
        delta = nd1 - 1.0

    # Gamma (same for call and put)
    gamma = pd1 / (spot * sigma * sqrt_T)

    # Theta (per day)
    common_theta = -(spot * pd1 * sigma) / (2 * sqrt_T)
    if is_call:
        theta = (common_theta - risk_free_rate * strike * math.exp(-risk_free_rate * T) * nd2) / 365.0
    else:
        theta = (common_theta + risk_free_rate * strike * math.exp(-risk_free_rate * T) * _cdf(-d2)) / 365.0

    # Vega (per 1% move in IV)
    vega = spot * pd1 * sqrt_T / 100.0

    # Rho (per 1% move in rate)
    if is_call:
        rho = strike * T * math.exp(-risk_free_rate * T) * nd2 / 100.0
    else:
        rho = -strike * T * math.exp(-risk_free_rate * T) * _cdf(-d2) / 100.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega":  round(vega, 4),
        "rho":   round(rho, 4),
    }


def enrich_options_with_greeks(
    options: list,
    spot: float,
    risk_free_rate: float = 0.045,
) -> list:
    """
    Add computed Greeks to each option dict in the chain.
    Expects each option dict to have: strike, impliedVolatility, dte, option_type.
    Writes greeks into a 'greeks' sub-dict matching the Tradier format.

    If yfinance IV looks suspicious (0, extreme, or produces a BS price far
    from the market mid), we re-derive IV from the market price.
    """
    for opt in options:
        try:
            strike = float(opt.get("strike") or 0)
            raw_iv = float(opt.get("impliedVolatility") or opt.get("iv") or 0)
            dte = int(opt.get("dte") or 0)
            opt_type = str(opt.get("option_type") or "call").lower()
            bid = float(opt.get("bid") or 0)
            ask = float(opt.get("ask") or 0)
            mid = (bid + ask) / 2.0

            iv = _clamp_iv(raw_iv)
            iv_was_corrected = False

            # ── IV validation: cross-check with market price ─────────────
            if iv > 0 and mid > 0 and spot > 0 and strike > 0 and dte > 0:
                bs_price = compute_bs_price(spot, strike, iv, dte, risk_free_rate, opt_type)
                # If BS price diverges >50% from market mid, IV is suspect
                if bs_price > 0 and abs(bs_price - mid) / mid > 0.50:
                    solved_iv = compute_iv_from_price(mid, spot, strike, dte, risk_free_rate, opt_type)
                    if solved_iv > 0:
                        iv = solved_iv
                        iv_was_corrected = True
            elif iv <= 0 and mid > 0 and spot > 0 and strike > 0 and dte > 0:
                # IV was zero — try to solve from market price
                solved_iv = compute_iv_from_price(mid, spot, strike, dte, risk_free_rate, opt_type)
                if solved_iv > 0:
                    iv = solved_iv
                    iv_was_corrected = True

            greeks = compute_greeks(spot, strike, iv, dte, risk_free_rate, opt_type)

            # Store in Tradier-compatible format for downstream consumers
            opt["greeks"] = {
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "theta": greeks["theta"],
                "vega":  greeks["vega"],
                "mid_iv": iv,
                "smv_vol": iv,
            }
            # Mark if IV was corrected so downstream can flag it
            opt["iv_corrected"] = iv_was_corrected
            if iv_was_corrected:
                opt["iv_original"] = raw_iv

        except Exception as e:
            log.debug(f"Greeks computation error: {e}")
            opt["greeks"] = {
                "delta": 0.0, "gamma": 0.0, "theta": 0.0,
                "vega": 0.0, "mid_iv": 0.0, "smv_vol": 0.0,
            }
            opt["iv_corrected"] = False

    return options
