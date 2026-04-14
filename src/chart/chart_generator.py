"""
src/chart/chart_generator.py
Generate candlestick chart snapshots with indicators for Discord embeds.
"""

import io
import logging
from typing import List, Optional

import numpy as np

log = logging.getLogger("ChartGenerator")


def generate_chart(
    symbol: str,
    bars: List[dict],
    signals=None,
    option_type: str = "call",
    strike: float = None,
) -> Optional[bytes]:
    """
    Generate a price chart with EMA, RSI, MACD, and support/resistance.
    Returns PNG bytes or None if generation fails.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.gridspec import GridSpec
        from matplotlib.patches import FancyBboxPatch
    except ImportError:
        log.warning("matplotlib not installed — skipping chart generation.")
        return None

    if not bars or len(bars) < 10:
        return None

    try:
        # ── Parse bars ────────────────────────────────────────────────────────
        def _f(b, *keys):
            for k in keys:
                if k in b:
                    try:
                        return float(b[k])
                    except Exception:
                        pass
            return 0.0

        dates  = list(range(len(bars)))
        opens  = np.array([_f(b, "o", "open")   for b in bars])
        highs  = np.array([_f(b, "h", "high")   for b in bars])
        lows   = np.array([_f(b, "l", "low")    for b in bars])
        closes = np.array([_f(b, "c", "close")  for b in bars])

        # ── Compute indicators ────────────────────────────────────────────────
        def ema(arr, period):
            k = 2 / (period + 1)
            result = np.zeros_like(arr)
            result[0] = arr[0]
            for i in range(1, len(arr)):
                result[i] = arr[i] * k + result[i - 1] * (1 - k)
            return result

        ema9  = ema(closes, 9)
        ema21 = ema(closes, 21)
        ema50 = ema(closes, 50)

        # RSI
        delta    = np.diff(closes)
        gain     = np.where(delta > 0, delta, 0.0)
        loss     = np.where(delta < 0, -delta, 0.0)
        avg_gain = np.convolve(gain, np.ones(14) / 14, mode='full')[:len(closes)]
        avg_loss = np.convolve(loss, np.ones(14) / 14, mode='full')[:len(closes)]
        with np.errstate(divide='ignore', invalid='ignore'):
            rs   = np.where(avg_loss == 0, 100, avg_gain / avg_loss)
        rsi      = 100 - (100 / (1 + rs))

        # MACD
        macd_line   = ema(closes, 12) - ema(closes, 26)
        signal_line = ema(macd_line, 9)
        macd_hist   = macd_line - signal_line

        # ── Dark theme ───────────────────────────────────────────────────────
        BG      = "#0d1117"
        PANEL   = "#161b22"
        GREEN   = "#00ff88"
        RED     = "#ff4d6d"
        YELLOW  = "#ffd60a"
        BLUE    = "#58a6ff"
        PURPLE  = "#d2a8ff"
        TEXT    = "#e6edf3"
        GRID    = "#21262d"
        ACCENT  = GREEN if option_type == "call" else RED

        fig = plt.figure(figsize=(14, 9), facecolor=BG)
        gs  = GridSpec(4, 1, figure=fig, hspace=0.05,
                       height_ratios=[4, 1, 1, 1])

        ax_price = fig.add_subplot(gs[0])
        ax_vol   = fig.add_subplot(gs[1], sharex=ax_price)
        ax_rsi   = fig.add_subplot(gs[2], sharex=ax_price)
        ax_macd  = fig.add_subplot(gs[3], sharex=ax_price)

        for ax in [ax_price, ax_vol, ax_rsi, ax_macd]:
            ax.set_facecolor(PANEL)
            ax.tick_params(colors=TEXT, labelsize=7)
            ax.spines['bottom'].set_color(GRID)
            ax.spines['top'].set_color(GRID)
            ax.spines['left'].set_color(GRID)
            ax.spines['right'].set_color(GRID)
            ax.yaxis.label.set_color(TEXT)
            ax.xaxis.label.set_color(TEXT)
            ax.grid(True, color=GRID, linewidth=0.5, alpha=0.5)

        # ── Candlesticks ──────────────────────────────────────────────────────
        width  = 0.6
        width2 = 0.1
        for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes)):
            color = GREEN if c >= o else RED
            ax_price.bar(i, abs(c - o), width, bottom=min(o, c), color=color, alpha=0.9)
            ax_price.bar(i, h - max(o, c), width2, bottom=max(o, c), color=color, alpha=0.7)
            ax_price.bar(i, min(o, c) - l, width2, bottom=l, color=color, alpha=0.7)

        # EMAs
        ax_price.plot(dates, ema9,  color=YELLOW,  linewidth=1.2, label="EMA 9",  alpha=0.9)
        ax_price.plot(dates, ema21, color=BLUE,    linewidth=1.2, label="EMA 21", alpha=0.9)
        ax_price.plot(dates, ema50, color=PURPLE,  linewidth=1.2, label="EMA 50", alpha=0.9)

        # Support / Resistance
        if signals:
            ax_price.axhline(signals.support,    color=GREEN, linestyle='--', linewidth=1, alpha=0.6, label=f"Support ${signals.support:.2f}")
            ax_price.axhline(signals.resistance, color=RED,   linestyle='--', linewidth=1, alpha=0.6, label=f"Resistance ${signals.resistance:.2f}")

        # Strike price line
        if strike:
            ax_price.axhline(strike, color=ACCENT, linestyle=':', linewidth=1.5, alpha=0.8, label=f"Strike ${strike:.0f}")

        ax_price.legend(loc="upper left", fontsize=7, facecolor=PANEL, labelcolor=TEXT, framealpha=0.8)
        ax_price.set_ylabel("Price", color=TEXT, fontsize=8)
        plt.setp(ax_price.get_xticklabels(), visible=False)

        # Title
        direction_emoji = "CALL" if option_type == "call" else "PUT"
        ax_price.set_title(
            f"  {symbol}  —  {direction_emoji}",
            color=TEXT, fontsize=13, fontweight="bold",
            loc="left", pad=10,
        )

        # ── Volume ────────────────────────────────────────────────────────────
        volumes = np.array([_f(b, "v", "volume") for b in bars])
        vol_colors = [GREEN if closes[i] >= opens[i] else RED for i in range(len(bars))]
        ax_vol.bar(dates, volumes, width, color=vol_colors, alpha=0.7)
        avg_vol = volumes[-20:].mean() if len(volumes) >= 20 else volumes.mean()
        ax_vol.axhline(avg_vol, color=YELLOW, linewidth=0.8, linestyle="--", alpha=0.7)
        ax_vol.set_ylabel("Vol", color=TEXT, fontsize=7)
        ax_vol.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M" if x >= 1e6 else f"{x/1e3:.0f}K"))
        plt.setp(ax_vol.get_xticklabels(), visible=False)

        # ── RSI ───────────────────────────────────────────────────────────────
        ax_rsi.plot(dates, rsi, color=BLUE, linewidth=1, alpha=0.9)
        ax_rsi.axhline(70, color=RED,   linewidth=0.8, linestyle="--", alpha=0.6)
        ax_rsi.axhline(30, color=GREEN, linewidth=0.8, linestyle="--", alpha=0.6)
        ax_rsi.axhline(50, color=TEXT,  linewidth=0.5, linestyle=":",  alpha=0.3)
        ax_rsi.fill_between(dates, rsi, 50, where=(rsi > 50), alpha=0.1, color=GREEN)
        ax_rsi.fill_between(dates, rsi, 50, where=(rsi < 50), alpha=0.1, color=RED)
        ax_rsi.set_ylim(0, 100)
        ax_rsi.set_ylabel("RSI", color=TEXT, fontsize=7)
        plt.setp(ax_rsi.get_xticklabels(), visible=False)

        # ── MACD ─────────────────────────────────────────────────────────────
        ax_macd.plot(dates, macd_line,   color=BLUE,   linewidth=1,   label="MACD")
        ax_macd.plot(dates, signal_line, color=YELLOW, linewidth=1,   label="Signal")
        hist_colors = [GREEN if v >= 0 else RED for v in macd_hist]
        ax_macd.bar(dates, macd_hist, width, color=hist_colors, alpha=0.6)
        ax_macd.axhline(0, color=TEXT, linewidth=0.5, alpha=0.4)
        ax_macd.set_ylabel("MACD", color=TEXT, fontsize=7)
        ax_macd.legend(loc="upper left", fontsize=6, facecolor=PANEL, labelcolor=TEXT, framealpha=0.7)

        # X-axis labels (show every Nth bar)
        step = max(1, len(bars) // 10)
        tick_positions = list(range(0, len(bars), step))
        ax_macd.set_xticks(tick_positions)
        ax_macd.set_xticklabels(
            [str(i) for i in tick_positions],
            fontsize=6, color=TEXT
        )

        # ── Pattern annotations ───────────────────────────────────────────────
        import re
        import warnings
        
        if signals and signals.patterns:
            pattern_text = " | ".join(signals.patterns[:2])
            # Strip emojis to prevent Matplotlib missing glyph warnings
            pattern_text = re.sub(r'[^\x00-\x7F]+', '', pattern_text).strip()
            
            ax_price.annotate(
                pattern_text,
                xy=(0.01, 0.02),
                xycoords="axes fraction",
                fontsize=7,
                color=ACCENT,
                fontweight="bold",
                alpha=0.9,
            )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            plt.tight_layout(rect=[0, 0, 1, 0.97])

        buf = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            plt.savefig(buf, format="png", dpi=130, facecolor=BG, bbox_inches="tight")
            
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        log.error(f"Chart generation failed for {symbol}: {e}", exc_info=True)
        return None
