#!/usr/bin/env python3
"""
Wall Street Level Trading Bot
Discord callout bot with options flow, directional bias, and chart snapshots
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime

import discord
from discord.ext import commands, tasks

from src.scanner.market_scanner import MarketScanner
from src.discord.callout_sender import CalloutSender
from src.data.market_hours import is_market_open, next_market_open
from config.settings import Settings

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)),
    ],
)
log = logging.getLogger("TradingBot")

# ── Bot Setup ─────────────────────────────────────────────────────────────────
settings = Settings()
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

scanner = MarketScanner(settings)
sender  = CalloutSender(bot, settings)


# ── Tasks ─────────────────────────────────────────────────────────────────────
@tasks.loop(minutes=10)
async def scan_loop():
    """Main scanning loop — runs every 10 min during market hours."""
    if not is_market_open():
        log.info("Market closed — skipping scan.")
        return

    log.info("🔍 Starting market scan...")
    try:
        callouts = await scanner.run_full_scan()
        if callouts:
            await sender.dispatch(callouts)
            log.info(f"✅ Dispatched {len(callouts)} callout(s).")
        else:
            log.info("No qualifying callouts this scan.")
    except Exception as e:
        log.error(f"Scan error: {e}", exc_info=True)


@tasks.loop(minutes=5)
async def flow_scan_loop():
    """Unusual options flow — runs every 5 min during market hours."""
    if not is_market_open():
        return
    try:
        flow_alerts = await scanner.scan_unusual_flow()
        if flow_alerts:
            await sender.dispatch_flow(flow_alerts)
    except Exception as e:
        log.error(f"Flow scan error: {e}", exc_info=True)


# ── Events ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    log.info(f"✅ Logged in as {bot.user} ({bot.user.id})")
    log.info(f"📡 Watching {len(settings.WATCHLIST)} tickers")
    scan_loop.start()
    flow_scan_loop.start()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="the market 📈"
        )
    )


# ── Commands ──────────────────────────────────────────────────────────────────
@bot.command(name="scan")
@commands.has_permissions(administrator=True)
async def force_scan(ctx):
    """Force an immediate scan."""
    await ctx.send("🔍 Running manual scan...")
    callouts = await scanner.run_full_scan()
    if callouts:
        await sender.dispatch(callouts)
        await ctx.send(f"✅ Found and dispatched **{len(callouts)}** callout(s).")
    else:
        await ctx.send("No qualifying setups found right now.")


@bot.command(name="quote")
async def quick_quote(ctx, ticker: str):
    """Get a quick quote + analysis for a ticker."""
    ticker = ticker.upper()
    await ctx.send(f"📊 Pulling data for **{ticker}**...")
    result = await scanner.analyze_single(ticker)
    if result:
        await sender.send_single(ctx.channel, result)
    else:
        await ctx.send(f"Could not analyze **{ticker}** — check the ticker or try later.")


@bot.command(name="flow")
async def check_flow(ctx, ticker: str):
    """Check unusual options flow for a specific ticker."""
    ticker = ticker.upper()
    flow = await scanner.get_flow_for_ticker(ticker)
    if flow:
        await sender.send_flow_single(ctx.channel, flow)
    else:
        await ctx.send(f"No unusual flow detected for **{ticker}**.")


@bot.command(name="watchlist")
async def show_watchlist(ctx):
    """Show the current watchlist."""
    tickers = ", ".join(settings.WATCHLIST)
    embed = discord.Embed(
        title="📋 Current Watchlist",
        description=f"```{tickers}```",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Scanning {len(settings.WATCHLIST)} tickers every 10 minutes")
    await ctx.send(embed=embed)


@bot.command(name="status")
async def bot_status(ctx):
    """Show bot status."""
    market_status = "🟢 OPEN" if is_market_open() else "🔴 CLOSED"
    embed = discord.Embed(title="🤖 Bot Status", color=discord.Color.green())
    embed.add_field(name="Market", value=market_status, inline=True)
    embed.add_field(name="Scan Interval", value="10 min", inline=True)
    embed.add_field(name="Flow Interval", value="5 min", inline=True)
    embed.add_field(name="Tickers Watched", value=str(len(settings.WATCHLIST)), inline=True)
    embed.add_field(name="Uptime", value=f"Since {bot.user.created_at.strftime('%Y-%m-%d')}", inline=True)
    await ctx.send(embed=embed)


# ── Graceful Shutdown ─────────────────────────────────────────────────────────
def handle_shutdown(sig, frame):
    log.info("Shutting down bot...")
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(bot.close())
    else:
        loop.run_until_complete(bot.close())

signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(settings.DISCORD_TOKEN, log_handler=None)
