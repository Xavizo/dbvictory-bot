"""Old flat module names, kept for one release after the move into dbv/.

Player-written actions and old notes may still say ``import potions`` or
``from protocol import ...``. Those names resolve to the moved module itself
(the same object, never a second copy), so state and settings stay shared.
Program code must use the dbv.* names; tests/test_source_layout.py checks it.
Remove this file one release after the move.
"""
import importlib
import importlib.abc
import importlib.util
import sys

ALIASES = {
    "adaptive_cavebot": "dbv.brain.cavebot.adaptive_cavebot",
    "adaptive_recording": "dbv.brain.cavebot.adaptive_recording",
    "atlas_scope": "dbv.brain.navigation.atlas_scope",
    "automation_domains": "dbv.brain.state.automation_domains",
    "barrier_band": "dbv.brain.combat.barrier_band",
    "barrier_spell": "dbv.brain.combat.barrier_spell",
    "bot_profiles": "dbv.brain.profiles",
    "cavebot": "dbv.brain.cavebot.cavebot",
    "cavebot_combat": "dbv.brain.cavebot.cavebot_combat",
    "cavebot_history": "dbv.brain.cavebot.cavebot_history",
    "cavebot_lure": "dbv.brain.cavebot.cavebot_lure",
    "cavebot_reports": "dbv.brain.cavebot.cavebot_reports",
    "cavebot_walking": "dbv.brain.cavebot.cavebot_walking",
    "check_client_build": "dbv.client_bridge.check_client_build",
    "chat_messages": "dbv.brain.state.chat_messages",
    "client_equipment": "dbv.client_bridge.client_equipment",
    "client_player_state": "dbv.client_bridge.client_player_state",
    "client_profiles": "dbv.client_bridge.client_profiles",
    "client_protocol": "dbv.client_bridge.client_protocol",
    "combat_reachability": "dbv.brain.combat.combat_reachability",
    "combat_spell_conditions": "dbv.brain.combat.combat_spell_conditions",
    "combat_wave": "dbv.brain.combat.combat_wave",
    "command_delivery": "dbv.client_bridge.command_delivery",
    "constants": "dbv.client_bridge.constants",
    "crypto": "dbv.client_bridge.crypto",
    "dashboard_api": "dbv.mcp_server.dashboard_api",
    "dashboard_security": "dbv.mcp_server.dashboard_security",
    "engine_recovery": "dbv.brain.state.engine_recovery",
    "farming_telemetry": "dbv.brain.hunts.farming_telemetry",
    "floor_step_witness": "dbv.brain.cavebot.floor_step_witness",
    "game_alerts": "dbv.brain.state.game_alerts",
    "game_pause_badge": "dbv.mcp_server.game_pause_badge",
    "game_state": "dbv.brain.state.game_state",
    "hunt_areas": "dbv.brain.hunts.hunt_areas",
    "hunt_comparison": "dbv.brain.hunts.hunt_comparison",
    "hunt_corridors": "dbv.brain.hunts.hunt_corridors",
    "hunt_encounters": "dbv.brain.hunts.hunt_encounters",
    "hunt_routes": "dbv.brain.hunts.hunt_routes",
    "hunt_sightings": "dbv.brain.hunts.hunt_sightings",
    "hunt_world": "dbv.brain.hunts.hunt_world",
    "hunter_knife": "dbv.brain.combat.hunter_knife",
    "hunter_knife_action": "dbv.brain.combat.hunter_knife_action",
    "item_observations": "dbv.brain.state.item_observations",
    "ki_loss": "dbv.brain.state.ki_loss",
    "manual_hunts": "dbv.brain.hunts.manual_hunts",
    "map_annotations": "dbv.brain.navigation.map_annotations",
    "map_atlas": "dbv.brain.navigation.map_atlas",
    "map_navigation": "dbv.brain.navigation.map_navigation",
    "mcp_server": "dbv.mcp_server.server",
    "message_history": "dbv.brain.state.message_history",
    "movement_history": "dbv.brain.cavebot.movement_history",
    "navigation_tools": "dbv.brain.navigation.navigation_tools",
    "packet_observer": "dbv.client_bridge.packet_observer",
    "passive_map": "dbv.brain.navigation.passive_map",
    "passive_map_win32": "dbv.client_bridge.passive_map_win32",
    "patcher": "dbv.client_bridge.patcher",
    "pause_hotkey": "dbv.mcp_server.pause_hotkey",
    "pet_tracking": "dbv.brain.state.pet_tracking",
    "potions": "dbv.brain.combat.potions",
    "protocol": "dbv.client_bridge.protocol",
    "proxy": "dbv.client_bridge.proxy",
    "recorded_rope": "dbv.brain.cavebot.recorded_rope",
    "recovery_preview": "dbv.brain.state.recovery_preview",
    "route_identity": "dbv.brain.cavebot.route_identity",
    "session_bridge": "dbv.client_bridge.session_bridge",
    "session_journal": "dbv.brain.state.session_journal",
    "session_lifetime": "dbv.brain.state.session_lifetime",
    "session_observations": "dbv.brain.state.session_observations",
    "session_startup": "dbv.brain.state.session_startup",
    "speed_up": "dbv.brain.combat.speed_up",
    "transition_learning": "dbv.brain.navigation.transition_learning",
    "transport_control": "dbv.mcp_server.transport_control",
    "transport_session": "dbv.mcp_server.transport_session",
    "tray_exit": "dbv.mcp_server.tray_exit",
    "world_minimap": "dbv.brain.navigation.world_minimap",
}


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name in ALIASES:
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module(ALIASES[spec.name])

    def exec_module(self, module):
        pass  # Already executed under its dbv name.


_FINDER = _AliasFinder()


def install():
    # First in line, so a stale flat copy left beside the new layout is never loaded.
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)
