# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = true
# ///
"""
Network Player Monitor v1.1.0 - Reverse Engineering Multiplayer

Hooks into NMS marker and chat systems to detect network players.
This is a research mod to understand how NMS stores multiplayer information.

FEATURES:
- Detects player join/leave via chat messages
- Hooks marker system to track player markers
- Identifies NetworkPlayer building class (0x39)
- Logs multiplayer state information with memory addresses

USAGE:
1. Enable multiplayer in NMS
2. Join or host a session
3. Watch the logs for player events

BINARY SEARCH STRINGS (for IDA/Ghidra):
- "has joined" / "has left" - Player join/leave messages
- "NetworkPlayer" - Network player references
- "Fireteam" - Fireteam/party references
- "%s joined your game" - Localized join message
- "cGcNetworkPlayerMarkerComponentData" - Component data
- "cGcMarkerList" - Marker list management

KEY OFFSETS (from MBIN exports):
- cGcApplication + 0xB508 = mbMultiplayerActive (bool)
- cGcPlayerStateData + 0x7CDB8 = MultiplayerLobbyID (uint64)
- cGcPlayerStateData + 0x7CDC0 = MultiplayerPrivileges (uint64)
- cGcPlayerStateData + 0x7F1D0 = MultiplayerUA (UniverseAddress)
- cGcSimulation + 0x24DE40 = mPlayer

BUILDING CLASS VALUES FOR NETWORK PLAYERS:
- 0x39 (57) = NetworkPlayer
- NetworkPlayerMarker scanner icon at offset 0x4D40
- NetworkPlayerMarkerShip at 0x4D78
- NetworkPlayerMarkerVehicle at 0x4DB0
- NetworkFSPlayerMarkers (fireteam) at 0x40C0
"""

import ctypes
import logging
from typing import Dict, Optional, Set
from datetime import datetime
from enum import IntEnum

from pymhf import Mod
from pymhf.gui.decorators import gui_button, gui_var
from pymhf.core.memutils import map_struct, get_addressof
import nmspy.data.types as nms
import nmspy.data.basic_types as basic
from nmspy.decorators import on_state_change
from nmspy.common import gameData

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)


# Building class enum values for network-related markers
class BuildingClass(IntEnum):
    """Building class values from cGcBuildingClassification enum."""
    NetworkPlayer = 0x39  # 57 decimal
    NetworkMonument = 0x3A  # 58 decimal


# Scanner icon types for filtering
class ScannerIconType(IntEnum):
    """Scanner icon types that indicate network players."""
    NetworkPlayerFireTeam = 0x1B
    NetworkPlayerFireTeamShip = 0x1C
    NetworkPlayer = 0x1D
    NetworkPlayerShip = 0x1E
    NetworkPlayerVehicle = 0x1F
    NetworkPlayerFireTeamFreighter = 0xE


class NetworkPlayerMonitor(Mod):
    """Monitor network players in No Man's Sky multiplayer sessions."""

    def __init__(self):
        super().__init__()
        self.connected_players: Dict[str, dict] = {}
        self.network_player_markers: Dict[str, int] = {}  # name -> marker address
        self.markers_added: int = 0
        self.markers_removed: int = 0
        self.chat_messages: list = []
        self.multiplayer_active: bool = False
        logger.info("NetworkPlayerMonitor initialized")

    # =========================================================================
    # STATE CHANGE HOOKS
    # =========================================================================

    @on_state_change("APPVIEW")
    def on_enter_game(self):
        """Called when entering the game world."""
        logger.info("=== Entered Game View ===")
        self._check_multiplayer_state()

    def _check_multiplayer_state(self):
        """Check current multiplayer state."""
        try:
            app = gameData.GcApplication
            if app:
                self.multiplayer_active = app.mbMultiplayerActive
                logger.info(f"Multiplayer Active: {self.multiplayer_active}")

                # Try to get lobby ID from player state
                player_state = gameData.player_state
                if player_state:
                    logger.info("Player state available")
                    # TODO: Read MultiplayerLobbyID at offset 0x7CDB8
        except Exception as e:
            logger.error(f"Error checking multiplayer state: {e}")

    # =========================================================================
    # CHAT MESSAGE HOOKS - Detect Join/Leave
    # =========================================================================

    @nms.cGcTextChatManager.Say.before
    def on_chat_message(
        self,
        this: ctypes._Pointer[nms.cGcTextChatManager],
        lsMessageBody: ctypes._Pointer[basic.cTkFixedString[0x3FF]],
        lbSystemMessage: bool,
    ):
        """Intercept chat messages to detect player join/leave."""
        if not lsMessageBody:
            return

        try:
            msg = str(lsMessageBody.contents)
            timestamp = datetime.now().isoformat()

            # Log all chat messages
            self.chat_messages.append({
                "time": timestamp,
                "message": msg,
                "is_system": lbSystemMessage
            })

            # Keep only last 50 messages
            if len(self.chat_messages) > 50:
                self.chat_messages = self.chat_messages[-50:]

            # Check for join/leave patterns
            if lbSystemMessage:
                logger.info(f"[SYSTEM] {msg}")

                # Common patterns (may vary by language)
                if "joined" in msg.lower() or "has joined" in msg.lower():
                    player_name = self._extract_player_name(msg, "joined")
                    if player_name:
                        self._on_player_joined(player_name)

                elif "left" in msg.lower() or "has left" in msg.lower():
                    player_name = self._extract_player_name(msg, "left")
                    if player_name:
                        self._on_player_left(player_name)
            else:
                logger.debug(f"[CHAT] {msg}")

        except Exception as e:
            logger.error(f"Error processing chat message: {e}")

    def _extract_player_name(self, message: str, action: str) -> Optional[str]:
        """Extract player name from join/leave message."""
        # Try common patterns
        # "[PlayerName] has joined your game"
        # "PlayerName has joined"
        try:
            msg_lower = message.lower()
            if action in msg_lower:
                # Split on the action word
                parts = message.split(action)
                if parts:
                    # Player name is usually before "has joined/left"
                    name_part = parts[0].strip()
                    # Remove brackets if present
                    name_part = name_part.strip("[]<>")
                    # Remove "has " if present
                    if name_part.lower().endswith(" has"):
                        name_part = name_part[:-4].strip()
                    return name_part if name_part else None
        except Exception as e:
            logger.error(f"Error extracting player name: {e}")
        return None

    def _on_player_joined(self, player_name: str):
        """Handle player join event."""
        logger.info(f"=== PLAYER JOINED: {player_name} ===")
        self.connected_players[player_name] = {
            "joined_at": datetime.now().isoformat(),
            "position": None,
            "status": "connected"
        }
        logger.info(f"Connected players: {list(self.connected_players.keys())}")

    def _on_player_left(self, player_name: str):
        """Handle player leave event."""
        logger.info(f"=== PLAYER LEFT: {player_name} ===")
        if player_name in self.connected_players:
            del self.connected_players[player_name]
        logger.info(f"Connected players: {list(self.connected_players.keys())}")

    # =========================================================================
    # MARKER HOOKS - Detect Network Player Markers
    # =========================================================================

    @nms.cGcMarkerList.TryAddMarker.after
    def on_marker_added(
        self,
        this: ctypes._Pointer[nms.cGcMarkerList],
        lPoint: ctypes._Pointer[nms.cGcMarkerPoint],
        lbUpdateTime: bool,
    ):
        """Hook called when a marker is added to the game."""
        self.markers_added += 1

        if not lPoint:
            return

        try:
            marker = lPoint.contents

            # Try to get marker information
            name = ""
            subtitle = ""
            position = None
            building_class = None
            marker_addr = 0

            try:
                marker_addr = get_addressof(marker)
            except:
                pass

            try:
                name = str(marker.mCustomName)
            except:
                pass

            try:
                subtitle = str(marker.mCustomSubtitle)
            except:
                pass

            try:
                pos = marker.mPosition
                position = f"({pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f})"
            except:
                pass

            try:
                building_class = int(marker.meBuildingClass)
            except:
                pass

            # Check if this is a DEFINITE network player marker (building class = 0x39)
            is_network_player = building_class == BuildingClass.NetworkPlayer

            # Log marker addition with full details for research
            if name or subtitle or is_network_player:
                class_name = f"NetworkPlayer(0x{building_class:X})" if is_network_player else f"0x{building_class:X}" if building_class else "None"
                logger.info(f"[MARKER+] Name: '{name}', Subtitle: '{subtitle}', "
                           f"Class: {class_name}, Position: {position}, Addr: 0x{marker_addr:X}")

            # Track network player markers
            if is_network_player:
                logger.info(f"*** NETWORK PLAYER MARKER DETECTED: {name} at {position} ***")
                if name and name not in self.connected_players:
                    self.connected_players[name] = {
                        "joined_at": datetime.now().isoformat(),
                        "position": position,
                        "status": "network_player_marker",
                        "source": "building_class_0x39",
                        "marker_address": f"0x{marker_addr:X}"
                    }
                    self.network_player_markers[name] = marker_addr

            # Also track by heuristics if building class not available
            elif name and len(name) > 0 and building_class is None:
                # Filter out common non-player markers
                skip_patterns = ["!", "SIGNAL_", "BUILDING_", "UI_", "BASE_",
                                "MISSION_", "WAYPOINT_", "MARKER_", "PLANET_",
                                "ICON_", "TARGET_", "SCAN_"]
                is_likely_player = not any(name.upper().startswith(p) for p in skip_patterns)

                if is_likely_player:
                    logger.info(f"*** POTENTIAL PLAYER MARKER (heuristic): {name} at {position} ***")
                    if name not in self.connected_players:
                        self.connected_players[name] = {
                            "joined_at": datetime.now().isoformat(),
                            "position": position,
                            "status": "marker_detected_heuristic",
                            "source": "name_filter",
                            "marker_address": f"0x{marker_addr:X}"
                        }

        except Exception as e:
            logger.error(f"Error processing marker addition: {e}")

    @nms.cGcMarkerList.RemoveMarker.after
    def on_marker_removed(
        self,
        this: ctypes._Pointer[nms.cGcMarkerList],
        lExampleMarker: ctypes._Pointer[nms.cGcMarkerPoint],
    ):
        """Hook called when a marker is removed."""
        self.markers_removed += 1

        if not lExampleMarker:
            return

        try:
            marker = lExampleMarker.contents
            name = ""

            try:
                name = str(marker.mCustomName)
            except:
                pass

            if name:
                logger.debug(f"[MARKER-] Removed: {name}")

        except Exception as e:
            logger.error(f"Error processing marker removal: {e}")

    # =========================================================================
    # GUI BUTTONS
    # =========================================================================

    @gui_button("Check Multiplayer State")
    def check_state(self):
        """Manual check of multiplayer state."""
        logger.info("=== MULTIPLAYER STATE CHECK ===")
        self._check_multiplayer_state()
        logger.info(f"Connected players: {list(self.connected_players.keys())}")
        logger.info(f"Markers added: {self.markers_added}")
        logger.info(f"Markers removed: {self.markers_removed}")
        logger.info(f"Recent chat messages: {len(self.chat_messages)}")

    @gui_button("List Connected Players")
    def list_players(self):
        """List all detected connected players."""
        logger.info("=== CONNECTED PLAYERS ===")
        if not self.connected_players:
            logger.info("No players detected")
        else:
            for name, info in self.connected_players.items():
                logger.info(f"  - {name} (joined: {info.get('joined_at', 'unknown')})")

    @gui_button("Show Recent Chat")
    def show_chat(self):
        """Show recent chat messages."""
        logger.info("=== RECENT CHAT ===")
        for msg in self.chat_messages[-10:]:
            prefix = "[SYS]" if msg.get("is_system") else "[MSG]"
            logger.info(f"{prefix} {msg.get('message', '')}")

    @gui_button("Clear Data")
    def clear_data(self):
        """Clear all tracked data."""
        self.connected_players.clear()
        self.network_player_markers.clear()
        self.chat_messages.clear()
        self.markers_added = 0
        self.markers_removed = 0
        logger.info("Data cleared")

    @gui_button("Dump Marker Info")
    def dump_markers(self):
        """Try to dump information about current markers."""
        logger.info("=== MARKER DUMP ===")
        logger.info(f"Total markers added: {self.markers_added}")
        logger.info(f"Total markers removed: {self.markers_removed}")
        logger.info(f"Net markers: {self.markers_added - self.markers_removed}")
        # TODO: If we can get access to the marker list, enumerate current markers

    @gui_button("Check Raw Multiplayer Data")
    def check_raw_mp_data(self):
        """Try to read raw multiplayer data from memory."""
        logger.info("=== RAW MULTIPLAYER DATA ===")

        try:
            app = gameData.GcApplication
            if not app:
                logger.info("GcApplication not available")
                return

            logger.info(f"mbMultiplayerActive: {app.mbMultiplayerActive}")

            # Try to get the app data
            if hasattr(app, 'mpData') and app.mpData:
                logger.info("mpData pointer available")

                # Try to access game state
                try:
                    app_data = app.mpData.contents
                    logger.info("mpData contents accessible")

                    # Try game state
                    if hasattr(app_data, 'mGameState'):
                        game_state = app_data.mGameState
                        logger.info("mGameState accessible")

                        # Try player state
                        if hasattr(game_state, 'mPlayerState'):
                            player_state = game_state.mPlayerState
                            logger.info("mPlayerState accessible")

                            # Log what we can access
                            logger.info(f"  Type: {type(player_state)}")

                except Exception as e:
                    logger.error(f"Error accessing mpData contents: {e}")

        except Exception as e:
            logger.error(f"Error in raw MP data check: {e}")

    @gui_button("Log Memory Addresses")
    def log_addresses(self):
        """Log memory addresses of key structures for debugging."""
        logger.info("=== MEMORY ADDRESSES ===")

        try:
            app = gameData.GcApplication
            if app:
                app_addr = get_addressof(app)
                logger.info(f"cGcApplication: 0x{app_addr:X}")

                # Multiplayer active flag should be at app + 0xB508
                mp_active_addr = app_addr + 0xB508
                logger.info(f"mbMultiplayerActive should be at: 0x{mp_active_addr:X}")

        except Exception as e:
            logger.error(f"Error getting addresses: {e}")

        try:
            sim = gameData.simulation
            if sim:
                sim_addr = get_addressof(sim)
                logger.info(f"cGcSimulation: 0x{sim_addr:X}")

                # Player should be at sim + 0x24DE40
                player_addr = sim_addr + 0x24DE40
                logger.info(f"mPlayer should be at: 0x{player_addr:X}")

        except Exception as e:
            logger.error(f"Error getting simulation address: {e}")

    @gui_button("Read Lobby ID (Experimental)")
    def read_lobby_id(self):
        """Try to read the MultiplayerLobbyID from player state memory.

        Offsets from exported_types.py:
        - cGcPlayerStateData + 0x7CDB8 = MultiplayerLobbyID (uint64)
        - cGcPlayerStateData + 0x7CDC0 = MultiplayerPrivileges (uint64)
        """
        logger.info("=== READING LOBBY ID ===")

        try:
            player_state = gameData.player_state
            if not player_state:
                logger.info("Player state not available")
                return

            player_state_addr = get_addressof(player_state)
            logger.info(f"cGcPlayerStateData base: 0x{player_state_addr:X}")

            # Calculate addresses for multiplayer fields
            lobby_id_addr = player_state_addr + 0x7CDB8
            privileges_addr = player_state_addr + 0x7CDC0

            logger.info(f"MultiplayerLobbyID should be at: 0x{lobby_id_addr:X}")
            logger.info(f"MultiplayerPrivileges should be at: 0x{privileges_addr:X}")

            # Try to read using ctypes
            try:
                lobby_id = ctypes.c_uint64.from_address(lobby_id_addr).value
                logger.info(f"MultiplayerLobbyID = {lobby_id} (0x{lobby_id:X})")
            except Exception as e:
                logger.error(f"Error reading lobby ID: {e}")

            try:
                privileges = ctypes.c_uint64.from_address(privileges_addr).value
                logger.info(f"MultiplayerPrivileges = {privileges} (0x{privileges:X})")
            except Exception as e:
                logger.error(f"Error reading privileges: {e}")

        except Exception as e:
            logger.error(f"Error in lobby ID read: {e}")

    @gui_button("Dump Network Player Markers")
    def dump_network_markers(self):
        """Dump all tracked network player markers with their addresses."""
        logger.info("=== NETWORK PLAYER MARKERS ===")

        if not self.network_player_markers:
            logger.info("No network player markers tracked")
        else:
            for name, addr in self.network_player_markers.items():
                logger.info(f"  - {name}: 0x{addr:X}")

        logger.info(f"\nDetection methods used:")
        for name, info in self.connected_players.items():
            logger.info(f"  - {name}: {info.get('source', 'unknown')}")

    @gui_button("Binary Search Helper")
    def binary_search_helper(self):
        """Output useful binary patterns and strings for reverse engineering."""
        logger.info("=== BINARY SEARCH PATTERNS ===")
        logger.info("")
        logger.info("Function signatures (from NMSpy types.py):")
        logger.info("  cGcMarkerList::TryAddMarker:")
        logger.info("    48 89 5C 24 ? 48 89 6C 24 ? 48 89 74 24 ? 48 89 7C 24 ? 41 54 41 56 41 57 48 83 EC ? F6 82")
        logger.info("")
        logger.info("  cGcMarkerList::RemoveMarker:")
        logger.info("    48 89 5C 24 ? 55 57 41 56 48 83 EC ? 40 32 ED")
        logger.info("")
        logger.info("  cGcTextChatManager::Say:")
        logger.info("    40 53 48 81 EC ? ? ? ? F3 0F 10 05")
        logger.info("")
        logger.info("String searches:")
        logger.info('  "has joined" / "has left"')
        logger.info('  "NetworkPlayer"')
        logger.info('  "Fireteam"')
        logger.info('  "cGcNetworkPlayerMarkerComponentData"')
        logger.info('  "%s joined your game"')
        logger.info("")
        logger.info("Key offsets:")
        logger.info("  cGcApplication + 0xB508 = mbMultiplayerActive")
        logger.info("  cGcPlayerStateData + 0x7CDB8 = MultiplayerLobbyID")
        logger.info("  cGcPlayerStateData + 0x7CDC0 = MultiplayerPrivileges")
        logger.info("  cGcMarkerPoint + 0x118 = meBuildingClass")
        logger.info("  BuildingClass.NetworkPlayer = 0x39 (57)")
