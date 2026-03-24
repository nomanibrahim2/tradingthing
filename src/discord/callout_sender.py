"""
src/discord/callout_sender.py
Formats OptionCallout objects into rich Discord embeds and
routes them to the correct channels based on confidence tier.
Now includes GEX, dark pool levels, and full indicator suite.
"""

import io
import logging
from datetime import datetime
from typing import List, Optional

import discord

from src.analysis.options_analyzer import OptionCallout
from config.settings import Settings

log = logging.getLogger("CalloutSender")


class CalloutSender:
    def __init__(self, bot: discord.Client, settings: Settings):
        self.bot = bot
        self.s   = settings

    # ── Dispatch ──────────────────────────────────────────────────────────
    async def dispatch(self, callout_dicts: List[dict]):
        for item in callout_dicts:
            try:
                await self._send_directional(
                    item["callout"], item.get("signals"), item.get("chart_bytes")
                )
            except Exception as e:
                log.error(f"Failed to send callout for {item['callout'].symbol}: {e}")

    async def dispatch_flow(self, flow_dicts: List[dict]):
        for item in flow_dicts:
            try:
                await self._send_flow(item["callout"], item.get("chart_bytes"))
            except Exception as e:
                log.error(f"Failed to send flow alert: {e}")

    async def send_single(self, channel, item: dict):
        await self._send_directional(
            item["callout"], item.get("signals"), item.get("chart_bytes"),
            override_channel=channel
        )

    async def send_flow_single(self, channel, item: dict):
        await self._send_flow(item["callout"], item.get("chart_bytes"),
                              override_channel=channel)

    # ── Directional callout embed ─────────────────────────────────────────
    async def _send_directional(
        self,
        callout: OptionCallout,
        signals,
        chart_bytes: Optional[bytes],
        override_channel=None,
    ):
        is_call   = callout.option_type.lower() == "call"
        type_str  = "CALL" if is_call else "PUT"
        dir_emoji = "📈" if is_call else "📉"

        color = (discord.Color.green()  if callout.confidence_tier == "HIGH"
                 else discord.Color.yellow() if callout.confidence_tier == "MEDIUM"
                 else discord.Color.red())

        embed = discord.Embed(
            title=f"{callout.confidence_color} {callout.symbol}  —  {type_str} ${callout.strike:.0f}  {dir_emoji}",
            description=f"**Trigger:** {callout.trigger}",
            color=color,
            timestamp=datetime.utcnow(),
        )

        # ── Trade details ─────────────────────────────────────────────────
        embed.add_field(
            name="📋 Option Details",
            value=(
                f"**Type:** {type_str}\n"
                f"**Strike:** ${callout.strike:.2f}\n"
                f"**Expiry:** {callout.expiration} ({callout.dte}d)\n"
                f"**Underlying:** ${callout.underlying_price:.2f}"
            ),
            inline=True,
        )

        embed.add_field(
            name="💰 Trade Levels",
            value=(
                f"**Entry:** ${callout.entry_price:.2f}\n"
                f"**Target (TP):** ${callout.target_price:.2f}\n"
                f"**Stop (SL):** ${callout.stop_loss:.2f}\n"
                f"**Bid/Ask:** ${callout.bid:.2f} / ${callout.ask:.2f}"
            ),
            inline=True,
        )

        embed.add_field(
            name="⚖️ Risk Metrics",
            value=(
                f"**Max Loss:** ${callout.max_loss:,.0f} /contract\n"
                f"**R:R Ratio:** {callout.reward_risk:.1f}:1\n"
                f"**Prob Profit:** {callout.prob_of_profit*100:.0f}%\n"
                f"**IV:** {callout.iv:.1f}%  [{callout.iv_vs_hv}]"
            ),
            inline=True,
        )

        # ── Greeks ────────────────────────────────────────────────────────
        embed.add_field(
            name="🔢 Greeks",
            value=(
                f"Δ Delta: `{callout.delta:+.3f}`\n"
                f"Γ Gamma: `{callout.gamma:.4f}`\n"
                f"Θ Theta: `{callout.theta:+.4f}`\n"
                f"ν Vega:  `{callout.vega:.4f}`"
            ),
            inline=True,
        )

        # ── Liquidity ─────────────────────────────────────────────────────
        embed.add_field(
            name="💧 Liquidity",
            value=(
                f"**OI:** {callout.open_interest:,}\n"
                f"**Vol:** {callout.volume:,}\n"
                f"**Spread:** ${callout.bid_ask_spread:.2f} ({callout.bid_ask_spread_pct:.1f}%)"
            ),
            inline=True,
        )

        # ── Momentum indicators ───────────────────────────────────────────
        if signals:
            rsi_e  = "🔥" if signals.rsi > 65 else ("❄️" if signals.rsi < 35 else "➡️")
            macd_e = "📈" if signals.macd_hist > 0 else "📉"
            cmf_e  = "🟢" if signals.cmf > 0.05 else ("🔴" if signals.cmf < -0.05 else "⚪")
            embed.add_field(
                name="📊 Momentum",
                value=(
                    f"**RSI:** {signals.rsi:.1f} {rsi_e}\n"
                    f"**Stoch %K/%D:** {signals.stoch_k:.0f} / {signals.stoch_d:.0f}\n"
                    f"**Williams %R:** {signals.williams_r:.0f}\n"
                    f"**MACD Hist:** {signals.macd_hist:+.4f} {macd_e}\n"
                    f"**CMF:** {signals.cmf:+.3f} {cmf_e}"
                ),
                inline=True,
            )

        # ── Trend / Volume ────────────────────────────────────────────────
        if signals:
            adx_e = "💪" if signals.adx > 30 else ("📶" if signals.adx > 20 else "💤")
            obv_e = "📈" if callout.obv_trend == "RISING" else ("📉" if callout.obv_trend == "FALLING" else "➡️")
            vwap_pos = "above" if signals.ema9 > signals.vwap else "below"
            embed.add_field(
                name="📈 Trend Strength",
                value=(
                    f"**ADX:** {signals.adx:.1f} {adx_e}  [{callout.trend_strength}]\n"
                    f"**+DI / -DI:** {signals.plus_di:.1f} / {signals.minus_di:.1f}\n"
                    f"**OBV:** {callout.obv_trend} {obv_e}\n"
                    f"**VWAP:** ${signals.vwap:.2f} (price {vwap_pos})"
                ),
                inline=True,
            )

        # ── EMAs ─────────────────────────────────────────────────────────
        if signals:
            embed.add_field(
                name="📉 Moving Averages",
                value=(
                    f"**EMA 9:**   ${signals.ema9:.2f}\n"
                    f"**EMA 21:**  ${signals.ema21:.2f}\n"
                    f"**EMA 50:**  ${signals.ema50:.2f}\n"
                    f"**EMA 200:** ${signals.ema200:.2f}"
                ),
                inline=True,
            )

        # ── Key Levels ────────────────────────────────────────────────────
        if signals:
            embed.add_field(
                name="🎯 Key Levels",
                value=(
                    f"**Support:**    ${signals.support:.2f}\n"
                    f"**Resistance:** ${signals.resistance:.2f}\n"
                    f"**BB Upper:**   ${signals.bb_upper:.2f}\n"
                    f"**BB Lower:**   ${signals.bb_lower:.2f}\n"
                    f"**ATR:** ${signals.atr:.2f} ({signals.atr_pct:.1f}%)"
                ),
                inline=True,
            )

        # ── Pivot Points ──────────────────────────────────────────────────
        if signals:
            embed.add_field(
                name="🔵 Pivot Points",
                value=(
                    f"**Pivot:** ${signals.pivot:.2f}\n"
                    f"**R1/R2:** ${signals.r1:.2f} / ${signals.r2:.2f}\n"
                    f"**S1/S2:** ${signals.s1:.2f} / ${signals.s2:.2f}\n"
                    f"**Cam R3/S3:** ${signals.cam_r3:.2f} / ${signals.cam_s3:.2f}"
                ),
                inline=True,
            )

        # ── GEX ───────────────────────────────────────────────────────────
        if callout.gex != 0 or callout.call_wall != 0:
            gex_fmt = f"${callout.gex/1e9:.2f}B" if abs(callout.gex) >= 1e9 else \
                      f"${callout.gex/1e6:.1f}M" if abs(callout.gex) >= 1e6 else \
                      f"${callout.gex:,.0f}"
            gex_e   = "📌" if callout.gex_bias == "LONG" else ("💥" if callout.gex_bias == "SHORT" else "⚪")
            embed.add_field(
                name=f"⚡ Gamma Exposure (GEX)  {gex_e}",
                value=(
                    f"**Net GEX:** {gex_fmt}  [{callout.gex_bias}]\n"
                    f"**GEX Flip:** ${callout.gex_flip:.2f}\n"
                    f"**Call Wall:** ${callout.call_wall:.2f}\n"
                    f"**Put Wall:**  ${callout.put_wall:.2f}\n"
                    f"**Max Pain:**  ${callout.max_pain:.2f}"
                ),
                inline=True,
            )

        # ── Dark Pool ─────────────────────────────────────────────────────
        if callout.dark_pool_levels:
            dp_lvls = "  |  ".join([f"${l:.2f}" for l in callout.dark_pool_levels[:4]])
            dp_e    = "🟢" if callout.dark_pool_bias == "BULLISH" else \
                      ("🔴" if callout.dark_pool_bias == "BEARISH" else "⚪")
            embed.add_field(
                name=f"🌑 Dark Pool Levels  {dp_e}  [{callout.dark_pool_bias}]",
                value=(
                    f"**Levels:** {dp_lvls}\n"
                    f"*(High-volume institutional price clusters — proxy via vol analysis)*"
                ),
                inline=False,
            )

        # ── Patterns ─────────────────────────────────────────────────────
        if callout.patterns:
            embed.add_field(
                name="🔍 Patterns Detected",
                value="\n".join(f"• {p}" for p in callout.patterns[:5]),
                inline=False,
            )

        # ── Confidence bar ────────────────────────────────────────────────
        filled  = int(callout.confidence * 10)
        bar     = "█" * filled + "░" * (10 - filled)
        conf_pct = int(callout.confidence * 100)
        embed.add_field(
            name=f"🎯 Confidence: {callout.confidence_tier}  [{bar}]  {conf_pct}%",
            value=f"Strategy: **{callout.strategy}**  |  Bias Score: `{callout.bias_score if hasattr(callout, 'bias_score') else '—'}`",
            inline=False,
        )

        embed.set_footer(
            text=f"Wall Street Scanner  •  {datetime.utcnow().strftime('%H:%M:%S')} UTC  |  ⚠️ Not financial advice"
        )

        # ── Chart ─────────────────────────────────────────────────────────
        if chart_bytes:
            embed.set_image(url=f"attachment://{callout.symbol}_chart.png")

        # ── Route to channels ─────────────────────────────────────────────
        channels_to_send = self._get_channels(callout, override_channel)
        for ch in channels_to_send:
            if chart_bytes:
                f = discord.File(io.BytesIO(chart_bytes), filename=f"{callout.symbol}_chart.png")
                await ch.send(embed=embed, file=f)
            else:
                await ch.send(embed=embed)

        log.info(f"Sent {callout.symbol} {type_str} ${callout.strike} → {len(channels_to_send)} channel(s).")

    # ── Flow embed ────────────────────────────────────────────────────────
    async def _send_flow(
        self,
        callout: OptionCallout,
        chart_bytes: Optional[bytes],
        override_channel=None,
    ):
        is_call   = callout.option_type.lower() == "call"
        type_str  = "CALL" if is_call else "PUT"
        premium   = callout.volume * callout.mid * 100
        flow_e    = "🐋" if premium >= 500_000 else "🦈"

        embed = discord.Embed(
            title=f"{flow_e} UNUSUAL FLOW  —  {callout.symbol}  {type_str}  ${callout.strike:.0f}",
            description=f"**Trigger:** {callout.trigger}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )

        embed.add_field(
            name="📋 Contract",
            value=(
                f"**Type:** {type_str}\n"
                f"**Strike:** ${callout.strike:.2f}\n"
                f"**Expiry:** {callout.expiration} ({callout.dte}d)\n"
                f"**Underlying:** ${callout.underlying_price:.2f}"
            ),
            inline=True,
        )

        embed.add_field(
            name="💸 Flow Size",
            value=(
                f"**Premium:** ${premium:,.0f}\n"
                f"**Volume:** {callout.volume:,}\n"
                f"**OI:** {callout.open_interest:,}\n"
                f"**Vol/OI:** {callout.volume / max(callout.open_interest, 1):.1f}x"
            ),
            inline=True,
        )

        embed.add_field(
            name="💰 Pricing",
            value=(
                f"**Mid:** ${callout.mid:.2f}\n"
                f"**Bid/Ask:** ${callout.bid:.2f} / ${callout.ask:.2f}\n"
                f"**Spread:** {callout.bid_ask_spread_pct:.1f}%\n"
                f"**IV:** {callout.iv:.1f}%"
            ),
            inline=True,
        )

        embed.add_field(
            name="🔢 Greeks",
            value=(
                f"Δ Delta: `{callout.delta:+.3f}`\n"
                f"Θ Theta: `{callout.theta:+.4f}`\n"
                f"ν Vega:  `{callout.vega:.4f}`"
            ),
            inline=True,
        )

        # GEX context on flow alerts
        if callout.gex != 0 or callout.call_wall != 0:
            gex_fmt = f"${callout.gex/1e6:.1f}M" if abs(callout.gex) >= 1e6 else f"${callout.gex:,.0f}"
            gex_e   = "📌" if callout.gex_bias == "LONG" else ("💥" if callout.gex_bias == "SHORT" else "⚪")
            embed.add_field(
                name=f"⚡ GEX Context  {gex_e}",
                value=(
                    f"**Net GEX:** {gex_fmt}  [{callout.gex_bias}]\n"
                    f"**Call Wall:** ${callout.call_wall:.2f}\n"
                    f"**Put Wall:**  ${callout.put_wall:.2f}\n"
                    f"**Max Pain:**  ${callout.max_pain:.2f}"
                ),
                inline=True,
            )

        # Dark pool context on flow alerts
        if callout.dark_pool_levels:
            dp_lvls = "  |  ".join([f"${l:.2f}" for l in callout.dark_pool_levels[:3]])
            dp_e    = "🟢" if callout.dark_pool_bias == "BULLISH" else \
                      ("🔴" if callout.dark_pool_bias == "BEARISH" else "⚪")
            embed.add_field(
                name=f"🌑 Dark Pool  {dp_e}  [{callout.dark_pool_bias}]",
                value=f"**Levels:** {dp_lvls}",
                inline=True,
            )

        # Trend context
        if callout.adx > 0:
            adx_e = "💪" if callout.adx > 30 else "📶"
            embed.add_field(
                name="📈 Trend",
                value=(
                    f"**ADX:** {callout.adx:.1f} {adx_e}  [{callout.trend_strength}]\n"
                    f"**OBV:** {callout.obv_trend}"
                ),
                inline=True,
            )

        if callout.notes:
            embed.add_field(name="📝 Notes", value=callout.notes, inline=False)

        embed.set_footer(
            text=f"Flow Scanner  •  {datetime.utcnow().strftime('%H:%M:%S')} UTC  |  ⚠️ Not financial advice"
        )

        channels_to_send = self._get_flow_channels(callout, override_channel)
        for ch in channels_to_send:
            await ch.send(embed=embed)

        log.info(f"Flow alert: {callout.symbol} {type_str} ${callout.strike} | ${premium:,.0f}")

    # ── Channel routing ───────────────────────────────────────────────────
    def _get_channels(self, callout: OptionCallout, override) -> list:
        if override:
            return [override]
        channels = []
        main_ch = self.bot.get_channel(self.s.CHANNEL_CALLOUTS)
        if main_ch:
            channels.append(main_ch)
        if callout.confidence_tier == "HIGH":
            high_ch = self.bot.get_channel(self.s.CHANNEL_HIGH_CONF)
            if high_ch:
                channels.append(high_ch)
        return channels

    def _get_flow_channels(self, callout: OptionCallout, override) -> list:
        if override:
            return [override]
        channels = []
        flow_ch = self.bot.get_channel(self.s.CHANNEL_FLOW)
        if flow_ch:
            channels.append(flow_ch)
        if callout.confidence_tier == "HIGH":
            main_ch = self.bot.get_channel(self.s.CHANNEL_CALLOUTS)
            if main_ch:
                channels.append(main_ch)
        return channels
