"""
src/analysis/greeks.py
Local Black-Scholes Greeks calculator.
Computes Delta, Gamma, Theta, Vega from spot, strike, IV, DTE, risk-free rate.
"""

import math
from typing import Dict, Optional

try:
    from scipy.stats import norm
except ImportError:
    # Fallback: manual approximation if scipy not installed
    norm = None


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
    if spot <= 0 or strike <= 0 or iv <= 0 or dte <= 0:
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
    """
    for opt in options:
        try:
            strike = float(opt.get("strike") or 0)
            iv = float(opt.get("impliedVolatility") or opt.get("iv") or 0)
            dte = int(opt.get("dte") or 0)
            opt_type = str(opt.get("option_type") or "call").lower()

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
        except Exception:
            opt["greeks"] = {
                "delta": 0.0, "gamma": 0.0, "theta": 0.0,
                "vega": 0.0, "mid_iv": 0.0, "smv_vol": 0.0,
            }

    return options
