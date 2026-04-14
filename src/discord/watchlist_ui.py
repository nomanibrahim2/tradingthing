import discord
from config.settings import DEFAULT_WATCHLIST, save_watchlist

class WatchlistAddModal(discord.ui.Modal, title="Add Tickers to Watchlist"):
    tickers = discord.ui.TextInput(
        label="Tickers (comma separated)",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. AAPL, TSLA, SPY",
        required=True,
        max_length=500
    )

    def __init__(self, current_settings):
        super().__init__()
        self.settings = current_settings

    async def on_submit(self, interaction: discord.Interaction):
        # Parse inputted tickers
        input_str = self.tickers.value.replace(" ", "")
        new_tickers = [t.upper() for t in input_str.split(",") if t]
        
        added = []
        for t in new_tickers:
            if t not in self.settings.WATCHLIST:
                self.settings.WATCHLIST.append(t)
                added.append(t)
        
        if added:
            save_watchlist(self.settings.WATCHLIST)
            await interaction.response.send_message(f"✅ Successfully added to watchlist: **{', '.join(added)}**", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ All provided tickers are already in the watchlist.", ephemeral=True)


class WatchlistRemoveModal(discord.ui.Modal, title="Remove Tickers from Watchlist"):
    tickers = discord.ui.TextInput(
        label="Tickers (comma separated)",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. AAPL, TSLA",
        required=True,
        max_length=500
    )

    def __init__(self, current_settings):
        super().__init__()
        self.settings = current_settings

    async def on_submit(self, interaction: discord.Interaction):
        input_str = self.tickers.value.replace(" ", "")
        del_tickers = [t.upper() for t in input_str.split(",") if t]
        
        removed = []
        for t in del_tickers:
            if t in self.settings.WATCHLIST:
                self.settings.WATCHLIST.remove(t)
                removed.append(t)
                
        if removed:
            save_watchlist(self.settings.WATCHLIST)
            await interaction.response.send_message(f"🗑️ Successfully removed from watchlist: **{', '.join(removed)}**", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ None of the provided tickers were found in the watchlist.", ephemeral=True)


class WatchlistView(discord.ui.View):
    def __init__(self, current_settings):
        super().__init__(timeout=None)
        self.settings = current_settings

    @discord.ui.button(label="Add Ticker(s)", style=discord.ButtonStyle.success, custom_id="wl_add")
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WatchlistAddModal(self.settings))

    @discord.ui.button(label="Remove Ticker(s)", style=discord.ButtonStyle.danger, custom_id="wl_remove")
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WatchlistRemoveModal(self.settings))

    @discord.ui.button(label="View Active List", style=discord.ButtonStyle.secondary, custom_id="wl_view")
    async def view_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_list = sorted(self.settings.WATCHLIST)
        total = len(current_list)
        msg = ", ".join(current_list)
        
        if len(msg) > 4000:
            msg = msg[:3997] + "..."
            
        embed = discord.Embed(
            title=f"📋 Active Watchlist ({total} Tickers)",
            description=f"```{msg}```",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Reset to Default", style=discord.ButtonStyle.danger, custom_id="wl_reset", row=1)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings.WATCHLIST.clear()
        self.settings.WATCHLIST.extend(DEFAULT_WATCHLIST)
        save_watchlist(self.settings.WATCHLIST)
        await interaction.response.send_message("🔄 Watchlist successfully restored to the default Top-100 factory set.", ephemeral=True)
