import json
import os
import logging

log = logging.getLogger("ServerManager")

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "guild_configs.json")

class ServerManager:
    def __init__(self):
        self.configs = self.load_configs()

    def load_configs(self) -> dict:
        """Loads guild configs from the json file."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.error(f"Error loading {CONFIG_FILE}: {e}")
        return {}

    def save_configs(self):
        """Saves current memory configs to the json file."""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.configs, f, indent=4)
        except Exception as e:
            log.error(f"Error saving {CONFIG_FILE}: {e}")

    def set_channel(self, guild_id: str, channel_type: str, channel_id: int):
        """
        Sets a specific channel type for a guild.
        Valid channel_types: 'callouts', 'high_conf', 'flow'
        """
        guild_id = str(guild_id)
        if guild_id not in self.configs:
            self.configs[guild_id] = {}
            
        self.configs[guild_id][channel_type] = channel_id
        self.save_configs()

    def get_channel(self, guild_id: str, channel_type: str) -> int:
        """Gets a channel ID for a specific guild and channel type. Returns None if not set."""
        guild_id = str(guild_id)
        if guild_id in self.configs:
            return self.configs[guild_id].get(channel_type)
        return None

    def get_all_channels_for_type(self, channel_type: str) -> list[int]:
        """Returns a list of all raw channel IDs registered across all guilds for a specific type."""
        channel_ids = []
        for guild_id, config in self.configs.items():
            ch_id = config.get(channel_type)
            if ch_id:
                channel_ids.append(ch_id)
        return channel_ids

server_manager = ServerManager()
