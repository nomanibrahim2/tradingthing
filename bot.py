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
from src.analysis.options_analyzer import OptionsAnalyzer
from src.discord.callout_sender import CalloutSender
from src.discord.watchlist_ui import WatchlistView
from config.settings import DEFAULT_WATCHLIST, save_watchlist
from src.data.market_hours import is_market_open, next_market_open
from config.settings import Settings
from config.server_manager import server_manager

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
        qualified = [c for c in callouts if c["callout"].confidence >= 0.70]
        
        if qualified:
            await sender.dispatch(qualified)
            log.info(f"✅ Dispatched {len(qualified)} callout(s) (confidence >= 70%).")
        else:
            if callouts:
                top = callouts[0]["callout"]
                log.info(f"Found {len(callouts)} callout(s) but none >= 70% confidence. Top: {top.symbol} {top.confidence:.0%}")
            else:
                log.info("No callouts found this scan.")
    except Exception as e:
        log.error(f"Scan error: {e}", exc_info=True)


@tasks.loop(minutes=5)
async def flow_scan_loop():
    """Unusual options flow — runs every 5 min during market hours."""
    if not is_market_open():
        return
    try:
        flow_alerts = await scanner.scan_unusual_flow()
        qualified_flow = [
            f for f in flow_alerts 
            if getattr(f["callout"], "conviction_score", getattr(f["callout"], "confidence", 0.0)) >= 0.70
        ]
        
        if qualified_flow:
            await sender.dispatch_flow(qualified_flow)
            log.info(f"✅ Dispatched {len(qualified_flow)} flow alert(s).")
        else:
            if flow_alerts:
                top = flow_alerts[0]["callout"]
                log.info(f"Found {len(flow_alerts)} flow alert(s) but none >= 70% conviction. Top: {top.symbol} {top.conviction_score:.0%}")
    except Exception as e:
        log.error(f"Flow scan error: {e}", exc_info=True)


# ── Startup Scan ──────────────────────────────────────────────────────────────
async def startup_scan():
    """Run an immediate full scan + flow scan on boot — no market hours gate."""
    log.info("🚀 Startup scan — sending today's callouts...")

    # ── Directional scan ──────────────────────────────────────────────────
    try:
        callouts = await scanner.run_full_scan()
        if callouts:
            await sender.dispatch(callouts)
            log.info(f"✅ Startup: dispatched {len(callouts)} directional callout(s).")
        else:
            log.info("Startup: no directional callouts found.")
    except Exception as e:
        log.error(f"Startup directional scan error: {e}", exc_info=True)

    # ── Flow scan ─────────────────────────────────────────────────────────
    try:
        flow_alerts = await scanner.scan_unusual_flow()
        qualified_flow = [
            f for f in flow_alerts
            if getattr(f["callout"], "conviction_score",
                       getattr(f["callout"], "confidence", 0.0)) >= 0.70
        ]
        if qualified_flow:
            await sender.dispatch_flow(qualified_flow)
            log.info(f"✅ Startup: dispatched {len(qualified_flow)} flow alert(s).")
        else:
            log.info("Startup: no qualifying flow alerts.")
    except Exception as e:
        log.error(f"Startup flow scan error: {e}", exc_info=True)

    log.info("🏁 Startup scan complete — live loops now active.")


# ── Events ────────────────────────────────────────────────────────────────────
_startup_done = False

@bot.event
async def on_ready():
    global _startup_done
    bot.add_view(WatchlistView(settings))
    log.info(f"✅ Logged in as {bot.user} ({bot.user.id})")
    log.info(f"📡 Watching {len(settings.WATCHLIST)} tickers")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="the market 📈"
        )
    )

    if not _startup_done:
        _startup_done = True
        # Run startup scan FIRST, then start recurring loops after it finishes
        await startup_scan()

    # Start the recurring live loops (they wait one full interval before first tick)
    if not scan_loop.is_running():
        scan_loop.start()
    if not flow_scan_loop.is_running():
        flow_scan_loop.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ **Missing argument:** `{error.param.name}`\nUsage example: `!{ctx.command.name} SPY`")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        log.error(f"Command error in '{ctx.command}': {error}")



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
        if result.get("callout"):
            await sender.send_single(ctx.channel, result)
        else:
            await sender.send_quote_overview(ctx.channel, result)
    else:
        await ctx.send(f"Could not analyze **{ticker}** — check the ticker or try later.")


@bot.command(name="flow")
async def check_flow(ctx, ticker: str):
    """Check unusual options flow for a specific ticker."""
    ticker = ticker.upper()
    flows = await scanner.get_flow_for_ticker(ticker, is_manual=True)
    if flows:
        top_flow = sorted(flows, key=lambda f: f["callout"].confidence, reverse=True)[0]
        await sender.send_flow_single(ctx.channel, top_flow)
    else:
        await ctx.send(f"No unusual flow detected for **{ticker}**.")
@bot.command(name="watchlist", aliases=["wl"])
async def show_watchlist_ui(ctx):
    """Open the interactive Watchlist UI."""
    embed = discord.Embed(
        title="📋 Scanner Watchlist Management",
        description="Click a button below to configure the tickers tracked by the automated scanner.",
        color=discord.Color.brand_green()
    )
    embed.set_footer(text=f"Currently tracking {len(settings.WATCHLIST)} tickers.")
    await ctx.send(embed=embed, view=WatchlistView(settings))






@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def interactive_setup(ctx):
    """Interactive wizard to set up all alerting channels."""
    prompts = [
        ("callouts", "Standard Callouts"),
        ("high_conf", "High Confidence Callouts"),
        ("flow", "Unusual Flow Alerts")
    ]
    
    await ctx.send("🔧 **Interactive Setup Wizard**\nLet's configure your bot. You can type `skip` at any step to keep the current channel or leave it blank.")
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    for channel_type, display_name in prompts:
        current_ch_id = server_manager.get_channel(str(ctx.guild.id), channel_type)
        curr_str = f"(Currently: <#{current_ch_id}>)" if current_ch_id else "(Not set)"
        
        await ctx.send(f"\n👉 Please `#mention` the text channel to use for **{display_name}** {curr_str}:")
        
        try:
            msg = await bot.wait_for('message', timeout=60.0, check=check)
            content_lower = msg.content.lower().strip()
            
            if content_lower == 'skip':
                await ctx.send(f"⏭️ Skipped **{display_name}**.")
                continue
                
            if msg.channel_mentions:
                target_channel = msg.channel_mentions[0]
            else:
                try:
                    ch_id = int(''.join(filter(str.isdigit, msg.content)))
                    target_channel = ctx.guild.get_channel(ch_id)
                except ValueError:
                    target_channel = None
                    
            if target_channel:
                server_manager.set_channel(str(ctx.guild.id), channel_type, target_channel.id)
                await ctx.send(f"✅ Bound **{display_name}** to {target_channel.mention}.")
                try:
                    await target_channel.send(f"🔗 This channel is now configured to receive **{display_name}**.")
                except Exception:
                    pass
            else:
                await ctx.send("⚠️ Could not recognize a channel. Skipping this step.")
                
        except asyncio.TimeoutError:
            await ctx.send("⏰ You took too long to answer. Setup wizard timed out. Run `!setup` again when you're ready!")
            return
            
    await ctx.send("\n🎉 **Setup Complete!** The bot is fully configured for this server.")

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
