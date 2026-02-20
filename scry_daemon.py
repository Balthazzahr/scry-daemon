#!/usr/bin/env python3
# ----------------------------------------------------------------------------
# MTGA LOG PARSER & TRACKER - FIXED VERSION
# Based on analysis of mtgatool and other professional trackers
# WITH CRITICAL BUG FIXES APPLIED:
#   1. Dynamic player seat detection
#   2. Correct opponent name extraction
#   3. Proper win/loss determination using teamId
#   4. Event deduplication to prevent duplicate messages
# ----------------------------------------------------------------------------

import json
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict
import sys
import re
from pathlib import Path
import atexit
import signal
from typing import Dict, List, Optional, Any
import threading
from queue import Queue
import urllib.request
import urllib.parse
import sqlite3
import glob
import config

# -----------------------------------------------------------------------------
# LOGGING HELPER
# -----------------------------------------------------------------------------
QUIET_MODE = False

def log(message, force=False):
    """Global log helper that respects QUIET_MODE"""
    if not QUIET_MODE or force:
        print(message, flush=True)

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
STATE_FILE = config.STATE_FILE
CARD_CACHE_FILE = config.CARD_CACHE_FILE
LOG_PATH = config.get_log_path() # Interactive path detection
POSSIBLE_PATHS = [LOG_PATH]      # Use selected path as the only possible path

# Set Release Dates
SET_RELEASES = [
    ("MKM", "2024-02-06"),
    ("OTJ", "2024-04-16"),
    ("MH3", "2024-06-11"),
    ("BLB", "2024-07-30"),
    ("DSK", "2024-09-24"),
    ("FDN", "2024-11-12"),
    ("DFT", "2025-02-11"),
    ("TBD", "2025-04-08"),
]

# Color mapping
COLOR_MAP = {
    1: 'W', 2: 'U', 3: 'B', 4: 'R', 5: 'G'
}

STR_COLOR_MAP = {
    "CardColor_White": "W", "ManaColor_White": "W",
    "CardColor_Blue": "U", "ManaColor_Blue": "U",
    "CardColor_Black": "B", "ManaColor_Black": "B",
    "CardColor_Red": "R", "ManaColor_Red": "R",
    "CardColor_Green": "G", "ManaColor_Green": "G",
}

# Card cache for mapping GRPIDs to card names
CARD_CACHE: Dict[int, Dict] = {}

def load_card_cache():
    """Load card cache from file or create empty cache"""
    global CARD_CACHE
    if CARD_CACHE_FILE.exists():
        try:
            with open(CARD_CACHE_FILE, 'r') as f:
                CARD_CACHE = {int(k): v for k, v in json.load(f).items()}
            log(f"[INFO] Loaded {len(CARD_CACHE)} cards from cache")
        except Exception as e:
            log(f"[WARNING] Could not load card cache: {e}")
            CARD_CACHE = {}
    else:
        CARD_CACHE = {}

def save_card_cache():
    """Save card cache to file"""
    try:
        CARD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CARD_CACHE_FILE, 'w') as f:
            json.dump(CARD_CACHE, f)
    except Exception as e:
        log(f"[WARNING] Could not save card cache: {e}")

def fetch_scryfall_card(grp_id: int, name: str = None) -> Optional[Dict]:
    """Fetch card data from Scryfall API using MTGA GRPID or Name fallback"""
    if name:
        # Try search by name first if provided and it's not a generic name
        if not name.startswith("Card#") and "Unknown" not in name:
            safe_name = urllib.parse.quote(name)
            url = f"https://api.scryfall.com/cards/named?fuzzy={safe_name}"
        else:
            url = f"https://api.scryfall.com/cards/arena/{grp_id}"
    else:
        url = f"https://api.scryfall.com/cards/arena/{grp_id}"

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MTGATrackerEnhanced/1.0'})
        with urllib.request.urlopen(req) as response:
            if response.getcode() == 200:
                data = json.loads(response.read().decode())
                # Extract image URL, handling double-faced cards
                image_url = None
                image_uris = data.get("image_uris")
                if image_uris:
                    image_url = image_uris.get("large") or image_uris.get("normal")
                elif "card_faces" in data:
                    # Check first face
                    face_uris = data["card_faces"][0].get("image_uris")
                    if face_uris:
                        image_url = face_uris.get("large") or face_uris.get("normal")

                # Extract useful info
                type_line = data.get("type_line", "")
                is_legendary = "Legendary" in type_line
                is_commander = is_legendary and ("Creature" in type_line or "Planeswalker" in type_line)
                
                card_info = {
                    "id": grp_id,
                    "name": data.get("name"),
                    "mana_cost": data.get("mana_cost"),
                    "colors": data.get("colors", []),
                    "color_identity": data.get("color_identity", []),
                    "type_line": type_line,
                    "image_url": image_url,
                    "scryfall_uri": data.get("scryfall_uri"),
                    "is_legendary": is_legendary,
                    "is_commander": is_commander
                }
                return card_info
    except urllib.error.HTTPError as e:
        # If name search failed, maybe try GRPID as last resort if we haven't already
        if name and url.startswith("https://api.scryfall.com/cards/named"):
             return fetch_scryfall_card(grp_id, name=None)
        if e.code == 404:
            return {"id": grp_id, "name": name or f"Unknown Card ({grp_id})", "color_identity": [], "not_found": True}
    except Exception as e:
        pass
    return None

def fetch_local_db_card(grp_id: int) -> Optional[Dict]:
    """Fetch card data from local MTGA SQLite database"""
    db_glob = "/mnt/Games/SteamLibrary/steamapps/common/MTGA/MTGA_Data/Downloads/Raw/Raw_CardDatabase_*.mtga"
    db_files = glob.glob(db_glob)
    if not db_files: return None
    
    INT_COLOR_MAP = {1: 'W', 2: 'U', 3: 'B', 4: 'R', 5: 'G'}
    
    try:
        conn = sqlite3.connect(db_files[0])
        cursor = conn.cursor()
        query = """
        SELECT L.Loc, C.ExpansionCode, C.Supertypes, C.Types, C.Colors, C.ColorIdentity, C.OldSchoolManaText
        FROM Cards C
        JOIN Localizations_enUS L ON C.TitleId = L.LocId
        WHERE C.GrpId = ?;
        """
        cursor.execute(query, (grp_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            name, set_code, supertypes, types, colors, color_id, mana_cost = row
            
            s_list = str(supertypes).split(',')
            t_list = str(types).split(',')
            
            # Supertype 2 = Legendary, Type 2 = Creature, Type 8 = Planeswalker
            is_legendary = '2' in s_list
            is_commander = is_legendary and ('2' in t_list or '8' in t_list)
            
            # Map MTGA internal type IDs to string for basic categorization
            # Artifact=1, Creature=2, Enchantment=3, Instant=4, Land=5, Sorcery=10, Planeswalker=8, Battle=11, Vanguard=13, Emblem=14
            type_map = {'1':'Artifact', '2':'Creature', '3':'Enchantment', '4':'Instant', '5':'Land', '10':'Sorcery', '8':'Planeswalker', '11':'Battle', '13':'Vanguard', '14':'Emblem'}
            type_line = " ".join([type_map[t] for t in t_list if t in type_map])
            if is_legendary:
                type_line = "Legendary " + type_line

            def map_colors(csv_str):
                if not csv_str: return []
                try:
                    parts = str(csv_str).split(',')
                    return sorted(list(set([INT_COLOR_MAP[int(c)] for c in parts if c.strip().isdigit() and int(c.strip()) in INT_COLOR_MAP])))
                except: return []

            return {
                "id": grp_id,
                "name": name,
                "set": set_code,
                "is_legendary": is_legendary,
                "is_commander": is_commander,
                "mana_cost": mana_cost,
                "type_line": type_line,
                "colors": map_colors(colors),
                "color_identity": map_colors(color_id)
            }
    except Exception as e:
        log(f"[DEBUG] Local DB lookup failed for {grp_id}: {e}")
    return None

def get_card_info(grp_id: int) -> Dict:
    """Get card info from GRPID, using cache -> Local DB -> Scryfall"""
    grp_id = int(grp_id)
    
    # Check cache first, but only if it has a real name
    if grp_id in CARD_CACHE:
        entry = CARD_CACHE[grp_id]
        if entry.get("name") and not entry.get("not_found") and "Unknown Card" not in entry.get("name") and not entry.get("name").startswith("Card#"):
            # REINFORCE: Ensure commander flags and color identity are present
            if "is_commander" not in entry or "is_legendary" not in entry or not entry.get("color_identity"):
                t_line = str(entry.get("type_line", ""))
                entry["is_legendary"] = entry.get("is_legendary", "Legendary" in t_line)
                entry["is_commander"] = entry.get("is_commander", entry["is_legendary"] and ("Creature" in t_line or "Planeswalker" in t_line))
                
                # FALLBACK: If color_identity is missing, derive it from mana_cost
                if not entry.get("color_identity"):
                    cost = entry.get("mana_cost", "")
                    derived = set()
                    for sym, col in [("W", "W"), ("U", "U"), ("B", "B"), ("R", "R"), ("G", "G")]:
                        if sym in cost: derived.add(col)
                    if derived:
                        entry["color_identity"] = sorted(list(derived))
            return entry
    
    # 1. Try local MTGA database (Absolute Ground Truth for Arena cards)
    card_info = fetch_local_db_card(grp_id)
    
    # 2. Try Scryfall if missing or to get image URLs/extra metadata
    if not card_info or not card_info.get("image_url"):
        name_hint = card_info.get("name") if card_info else None
        scry_info = fetch_scryfall_card(grp_id, name=name_hint)
        if scry_info:
            if card_info:
                # Update but keep original ID and NAME if they were correct
                orig_id = card_info.get("id")
                orig_name = card_info.get("name")
                card_info.update(scry_info)
                if orig_id: card_info["id"] = orig_id
                if orig_name: card_info["name"] = orig_name
            else:
                card_info = scry_info
    
    if card_info:
        CARD_CACHE[grp_id] = card_info
        # Only save if we found a real name
        name = card_info.get("name", "")
        if name and "Unknown Card" not in name and not name.startswith("Card#"):
            save_card_cache()
        return card_info
    
    # Ultimate fallback (Generic)
    return {"id": grp_id, "name": f"Card#{grp_id}", "colors": [], "color_identity": [], "is_legendary": False, "is_commander": False}

def get_card_info_by_name(name: str) -> Dict:
    """Find card info in cache by name fallback"""
    if not name or "Unknown" in name or name.startswith("Card#"):
        return {}
    # Iterate through cache (values are dicts)
    for info in CARD_CACHE.values():
        if info.get("name") == name:
            return info
    return {}

def get_card_name(grp_id: int) -> str:
    """Get card name from GRPID"""
    return get_card_info(grp_id).get('name', f'Card#{grp_id}')

def find_log_file():
    """Iterates through known paths to find the Player.log"""
    for path in POSSIBLE_PATHS:
        if path.exists():
            return path
    return None

def follow(file_path, initial_seek_end=True, check_interval=1):
    """Generator that yields new lines from a file, ensuring complete lines."""
    
    f = open(file_path, "r", encoding="utf-8", errors='replace')
    
    try:
        if initial_seek_end:
            f.seek(0, os.SEEK_END)
        else:
            f.seek(0)

        st_dev = os.fstat(f.fileno()).st_dev
        st_ino = os.fstat(f.fileno()).st_ino
        
        last_check_time = time.time()
        last_heartbeat = time.time()
        
        while True:
            where = f.tell()
            line = f.readline()
            
            if not line:
                current_time = time.time()
                
                # Heartbeat every 10 minutes
                if current_time - last_heartbeat >= 600:
                    last_heartbeat = current_time
                    log(f"[HEARTBEAT] Still monitoring {file_path} (Active for {int((current_time - last_check_time)/60)}m)")

                if current_time - last_check_time >= check_interval:
                    last_check_time = current_time
                    try:
                        current_st = os.stat(file_path)
                        if current_st.st_dev != st_dev or current_st.st_ino != st_ino or f.tell() > current_st.st_size:
                            log(f"[INFO] Log file rotated or truncated. Reopening {file_path}")
                            f.close()
                            f = open(file_path, "r", encoding="utf-8", errors='replace')
                            st_dev = os.fstat(f.fileno()).st_dev
                            st_ino = os.fstat(f.fileno()).st_ino
                            f.seek(0)
                    except FileNotFoundError:
                        log(f"[WARNING] Log file {file_path} not found. Waiting for it to reappear.")
                        f.close()
                        while not Path(file_path).exists():
                            time.sleep(check_interval)
                        f = open(file_path, "r", encoding="utf-8", errors='replace')
                        st_dev = os.fstat(f.fileno()).st_dev
                        st_ino = os.fstat(f.fileno()).st_ino
                        f.seek(0)
                    except Exception as e:
                        log(f"[ERROR] Error checking log file status: {e}")
                
                time.sleep(0.1)
                continue
            
            if not line.endswith('\n'):
                f.seek(where)
                time.sleep(0.1)
                continue
            
            if "DETAILED LOGS: DISABLED" in line:
                log("\n⚠️  WARNING: Detailed Logs are DISABLED in MTGA!", force=True)
                log("   Please enable 'Detailed Logs (Plugin Support)' in MTGA Settings -> Account.", force=True)

            yield line
    finally:
        f.close()


class JSONBuffer:
    """Enhanced JSON buffer handler inspired by mtgatool's approach"""
    
    def __init__(self):
        self.buffer = ""
        self.depth = 0
        self.in_json = False
        
    def add_line(self, line: str) -> Optional[Dict]:
        """Add a line and try to extract complete JSON"""
        stripped = line.strip()
        
        # Check if this line contains JSON markers
        if '{' in stripped or self.in_json:
            self.buffer += stripped
            self.depth += stripped.count('{') - stripped.count('}')
            self.in_json = True
            
            # If we've closed all braces, try to parse
            if self.depth <= 0 and self.buffer:
                result = self._try_parse()
                self.reset()
                return result
                
        return None
    
    def _try_parse(self) -> Optional[Dict]:
        """Try to parse the buffer as JSON"""
        if not self.buffer:
            return None
            
        try:
            # Try to find the JSON portion
            start = self.buffer.find('{')
            if start == -1:
                return None
                
            json_str = self.buffer[start:]
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try to find the last complete JSON object
            last_brace = json_str.rfind('}')
            if last_brace != -1:
                try:
                    return json.loads(json_str[:last_brace + 1])
                except json.JSONDecodeError:
                    pass
        return None
    
    def reset(self):
        """Reset the buffer"""
        self.buffer = ""
        self.depth = 0
        self.in_json = False


class LogEntryHandler:
    """Handler for different types of log entries - inspired by mtgatool's logEntrySwitch"""
    
    def __init__(self, tracker):
        self.tracker = tracker
        
        # Map of keywords to handler methods
        self.handlers = {
            'authenticateResponse': self.handle_auth,
            'deckSubmit': self.handle_deck_submit,
            'matchCreated': self.handle_match_created,
            'matchGameRoomStateChangedEvent': self.handle_game_room_state,
            'ConnectResp': self.handle_connect,
            'MulliganReq': self.handle_mulligan,
            'mulliganResp': self.handle_mulligan,
            'ClientMessageType_MulliganResp': self.handle_mulligan,
            'IntermissionReq': self.handle_intermission,
            'turnInfo': self.handle_turn_info,
            'GameStateMessage': self.handle_game_state,
            'winningTeamId': self.handle_game_end,
            'rankUpdate': self.handle_rank_update,
            'RankUpdated': self.handle_rank_update,
            'ClientToGreMessage': self.handle_client_message,
            'GreToClientEvent': self.handle_gre_event,
            'greToClientMessages': self.handle_gre_messages,
            'Client.SceneChange': self.handle_scene_change,
            'PlayerInventory.GetPlayerInventory': self.handle_inventory,
            'PlayerInventory.GetPlayerCards': self.handle_player_cards,
            'DeckUpsertDeckV2': self.handle_deck_v2,
            'EventSetDeckV2': self.handle_deck_v2,
            'EventSetDeck': self.handle_deck_v2,
            'Deck.SetDeck': self.handle_deck_v2,
            'EventGetCoursesV2': self.handle_course_deck,
            'DeckGetDeckSummariesV2': self.handle_deck_summaries,
            'DeckGetDeckDetailsV2': self.handle_deck_details,
            'CourseDeckSummary': self.handle_course_deck,
        }

        # Keywords that MUST be at the top level to avoid false positives from nested data
        self.STRICT_KEYWORDS = {
            'CourseDeckSummary', 'DeckUpsertDeckV2', 'EventSetDeckV2', 'EventSetDeck',
            'DeckGetDeckSummariesV2', 'DeckGetDeckDetailsV2'
        }
    
    def process(self, data: Dict, timestamp: float, event_name: str = None):
        """Process a data entry by routing to appropriate handler"""
        self.tracker.current_log_time = timestamp
        
        # 1. If we have a specific event name from the log prefix, try that first
        if event_name and event_name in self.handlers:
            try:
                self.handlers[event_name](data)
                return # Successfully handled by specific event name
            except Exception as e:
                self.tracker.log(f"[ERROR] Error in specific handler {event_name}: {e}")

        # 2. Fallback: Check each handler to see if it should process this data based on keywords
        for keyword, handler in self.handlers.items():
            is_match = False
            if keyword in self.STRICT_KEYWORDS:
                # For strict keywords, only match if it's at the top level
                if isinstance(data, dict) and keyword in data:
                    is_match = True
            else:
                # For other keywords, use the recursive search
                if self._data_contains(data, keyword):
                    is_match = True
            
            if is_match:
                try:
                    handler(data)
                except Exception as e:
                    self.tracker.log(f"[ERROR] Error in handler {keyword}: {e}")
                    
    def _data_contains(self, data: Any, keyword: str) -> bool:
        """Recursively check if data contains a keyword"""
        if isinstance(data, dict):
            if keyword in data:
                return True
            for value in data.values():
                if self._data_contains(value, keyword):
                    return True
        elif isinstance(data, list):
            for item in data:
                if self._data_contains(item, keyword):
                    return True
        elif isinstance(data, str):
            return keyword in data
        return False
    
    def handle_auth(self, data):
        """Handle authentication response"""
        auth = self.tracker.find_val(data, "authenticateResponse")
        if auth:
            self.tracker.hero_identity["playerId"] = auth.get("clientId")
            self.tracker.hero_identity["screenName"] = auth.get("screenName")
            login_time = datetime.fromtimestamp(self.tracker.current_log_time).strftime('%I:%M %p')
            self.tracker.log(f"[INFO] Connected to Arena as {self.tracker.hero_identity['screenName']} at {login_time}.")
    
    def handle_deck_submit(self, data):
        """Handle deck submission"""
        deck_submit = self.tracker.find_val(data, "deckSubmit")
        if deck_submit:
            name = deck_submit.get("deckName")
            if name and name != self.tracker.current_match["deckName"]:
                self.tracker.current_match["deckName"] = name
                self.tracker.last_deck_name = name
                
                deck_data = deck_submit.get("deck")
                if deck_data:
                    self.tracker.current_deck_info = deck_data
                    colors = self.tracker.extract_deck_colors(deck_data)
                    self.tracker.current_match["deckColors"] = colors
                    self.tracker.last_deck_colors = colors
                    
                    main_deck = deck_data.get("mainDeck", [])
                    total = sum(card.get("quantity", 0) for card in main_deck)
                    self.tracker.current_match["totalCards"] = total
                    
                    # Cache cards
                    for card in main_deck:
                        grp_id = card.get("grpId")
                        if grp_id and grp_id not in CARD_CACHE:
                            CARD_CACHE[grp_id] = {"id": grp_id}
                    
                    color_str = self.tracker.format_colors(colors)
                    self.tracker.log(f"[INFO] Using Deck: {name} ({color_str}, {total} cards)")
    
    def handle_match_created(self, data):
        """Handle match created event"""
        self.tracker.handle_gre_message(data)
    
    def handle_game_room_state(self, data):
        """Handle game room state changed event"""
        inner = data.get("matchGameRoomStateChangedEvent")
        if inner:
            # We want handle_gre_message to see the whole wrapper for context if possible
            # or at least the part it knows how to parse
            self.tracker.handle_gre_message(data)
        else:
            self.tracker.handle_gre_message(data)
    
    def handle_connect(self, data):
        """Handle connect response"""
        self.tracker.handle_gre_message(data)
        self.tracker.write_waybar_json()
    
    def handle_mulligan(self, data):
        """Handle mulligan request and response"""
        # Check for MulliganResp (player's decision)
        mulligan_resp = self.tracker.find_val(data, "mulliganResp")
        if mulligan_resp:
            decision = mulligan_resp.get("decision", "")
            if decision != self.tracker.current_match["last_mulligan_decision"]:
                self.tracker.current_match["last_mulligan_decision"] = decision
                if decision == "MulliganOption_Mulligan":
                    self.tracker.current_match["mulligans"] += 1
                    self.tracker.log(f"[INFO] You mulliganed (Total: {self.tracker.current_match['mulligans']})")
                elif decision == "MulliganOption_AcceptHand":
                    self.tracker.log(f"[INFO] You kept your hand")
        
        # Also handle the original MulliganReq
        self.tracker.handle_gre_message(data)
    
    def handle_intermission(self, data):
        """Handle intermission (opening hand)"""
        self.tracker.handle_gre_message(data)
    
    def handle_turn_info(self, data):
        """Handle turn info"""
        self.tracker.handle_gre_message(data)
    
    def handle_game_state(self, data):
        """Handle game state message"""
        game_state_msg = self.tracker.find_val(data, "gameStateMessage")
        if game_state_msg:
            # HEARTBEAT: If we are receiving game states, we are in a match
            if not self.tracker.current_match["active"]:
                game_objects = game_state_msg.get("gameObjects", [])
                if game_objects:
                    self.tracker.current_match["active"] = True
                    self.tracker.log("[INFO] Game Session: ACTIVE")
                    self.tracker.write_waybar_json()

            # Extract variant from gameInfo
            game_info = game_state_msg.get("gameInfo", {})
            if game_info:
                variant = game_info.get("variant")
                if variant and variant != self.tracker.current_game_variant:
                    self.tracker.current_game_variant = variant
                    old_format = self.tracker.current_match["format"]
                    self.tracker.update_match_format(game_info)
                    if self.tracker.current_match["format"] != "Unknown" and self.tracker.current_match["format"] != old_format:
                        self.tracker.log(f"[INFO] Format detected: {self.tracker.current_match['format']}")
        
        self.tracker.handle_gre_message(data)
    
    def handle_game_end(self, data):
        """Handle game end"""
        self.tracker.handle_gre_message(data)
    
    def handle_rank_update(self, data):
        """Handle rank update"""
        rank_update = self.tracker.find_val(data, "rankUpdate") or self.tracker.find_val(data, "RankUpdated")
        if rank_update:
            old_class = rank_update.get("oldClass", "Unknown")
            old_level = rank_update.get("oldLevel", 0)
            new_class = rank_update.get("newClass") or rank_update.get("updatedRankClass", "Unknown")
            new_level = rank_update.get("newLevel") or rank_update.get("updatedRankLevel", 0)
            
            self.tracker.current_match["rank"] = {
                "class": new_class,
                "tier": new_level,
                "step": rank_update.get("newStep", 0)
            }
            
            if old_class != new_class or old_level != new_level:
                self.tracker.log(f"[INFO] Rank: {old_class} Tier {old_level} → {new_class} Tier {new_level}")
                self.tracker.current_match["rankChange"] = {
                    "from": {"class": old_class, "tier": old_level},
                    "to": {"class": new_class, "tier": new_level}
                }
    
    def handle_client_message(self, data):
        """Handle client to GRE message"""
        self.tracker.handle_client_message(data.get("clientToGreMessage", data))
    
    def handle_gre_event(self, data):
        """Handle GRE to client event"""
        event = data.get("greToClientEvent", {})
        if isinstance(event, str):
            try:
                event = json.loads(event)
            except:
                pass
        
        if isinstance(event, dict):
            messages = event.get("greToClientMessages", [])
            for msg in messages:
                self.tracker.handle_gre_message(msg)
    
    def handle_scene_change(self, data):
        """Handle scene change events to detect if we are in a match"""
        scene_data = data.get("Client.SceneChange") or data
        to_scene = scene_data.get("toSceneName")
        
        if to_scene in ["Home", "DeckListViewer", "Store", "Collection", "Social", "Lobby"]:
            if self.tracker.current_match["active"]:
                self.tracker.log(f"[INFO] Scene change to {to_scene} detected. Setting status to LOBBY.")
                self.tracker.current_match["active"] = False
                self.tracker.write_waybar_json()

    def handle_gre_messages(self, data):
        """Handle multiple GRE messages"""
        messages = data.get("greToClientMessages", [])
        for msg in messages:
            self.tracker.handle_gre_message(msg)
    
    def handle_inventory(self, data):
        """Handle player inventory - can be extended to track collection"""
        pass
    
    def handle_player_cards(self, data):
        """Handle player cards - update card cache"""
        cards_data = self.tracker.find_val(data, "cards")
        if cards_data and isinstance(cards_data, list):
            for card in cards_data:
                grp_id = card.get("grpId")
                if grp_id:
                    CARD_CACHE[grp_id] = {
                        "id": grp_id,
                        "name": card.get("name", f"Card#{grp_id}"),
                    }
            save_card_cache()

    def handle_deck_v2(self, data):
        """Handle DeckUpsertDeckV2 or EventSetDeckV2 messages"""
        payload = data
        
        # If there's a request field, it's often an escaped JSON string
        request_str = data.get("request")
        if request_str and isinstance(request_str, str) and request_str.startswith("{"):
            try:
                payload = json.loads(request_str)
            except:
                pass

        # Extract Summary and Deck info
        summary = payload.get("Summary")
        deck_data = payload.get("Deck")
        
        # Fallback for different structures
        if not summary:
            summary = self.tracker.find_val(data, "Summary")
        if not deck_data:
            deck_data = self.tracker.find_val(data, "Deck")

        if summary:
            name = summary.get("Name")
            if name:
                self.tracker.update_deck_name(name)
        
        if deck_data:
            # Extract Commander from CommandZone
            command_zone = deck_data.get("CommandZone", [])
            if command_zone and len(command_zone) > 0:
                # Structure is often [{"cardId": 123, "quantity": 1}] or [123, 456]
                first = command_zone[0]
                grp_id = None
                if isinstance(first, dict):
                    grp_id = first.get("cardId") or first.get("grpId")
                elif isinstance(first, (int, str)):
                    grp_id = int(first)
                
                if grp_id:
                    self.tracker.current_match["heroCommanderId"] = grp_id
                    info = get_card_info(grp_id)
                    commander_name = info.get("name", f"Card#{grp_id}")
                    if self.tracker.current_match["heroCommander"] != commander_name:
                        self.tracker.current_match["heroCommander"] = commander_name
                        self.tracker.log(f"[INFO] Player's Commander Identified: {commander_name} [{''.join(self.tracker.current_match['deckColors'])}]")
                    
                    # Update colors from commander identity or name fallback
                    identity = info.get("color_identity", [])
                    if not identity:
                        # Fallback to name search in cache
                        name_info = get_card_info_by_name(commander_name)
                        identity = name_info.get("color_identity", [])
                    
                    if identity:
                        self.tracker.current_match["deckColors"] = sorted(identity)
                        self.tracker.last_deck_colors = self.tracker.current_match["deckColors"]

                    # If it's a Brawl deck, ensure name reflects commander
                    if not self.tracker.is_generic_name(self.tracker.current_match["deckName"]):
                        pass # Keep existing specific name
                    else:
                        self.tracker.update_deck_name(f"Brawl: {commander_name}")

            # Update colors
            colors = self.tracker.extract_deck_colors(deck_data)
            if colors:
                self.tracker.current_match["deckColors"] = colors
                self.tracker.last_deck_colors = colors

        self.tracker.write_waybar_json()

    def handle_course_deck(self, data):
        """Handle CourseDeckSummary messages - Only if match is active/starting"""
        # If this is part of EventGetCoursesV2, we need to find the specific course for the current match
        if "Courses" in data:
            target_course = self.tracker.current_match.get("eventId")
            for course in data["Courses"]:
                # If we have an active match, prioritize the matching eventId
                # If not, the first one is usually the most recent or active one
                is_target = (target_course and course.get("CourseId") == target_course)
                
                summary = course.get("CourseDeckSummary")
                deck_data = course.get("CourseDeck")
                
                if summary:
                    name = summary.get("Name")
                    if name:
                        self.tracker.update_deck_name(name)
                
                if deck_data:
                    # Extract Commander
                    command_zone = deck_data.get("CommandZone", [])
                    if command_zone:
                        first = command_zone[0]
                        grp_id = first.get("cardId") if isinstance(first, dict) else first
                        if grp_id:
                            self.tracker.current_match["heroCommanderId"] = grp_id
                            info = get_card_info(grp_id)
                            commander_name = info.get("name", f"Card#{grp_id}")
                            if self.tracker.current_match["heroCommander"] != commander_name:
                                self.tracker.current_match["heroCommander"] = commander_name
                                self.tracker.log(f"[INFO] Hero Commander from Course: {commander_name} [{''.join(self.tracker.current_match['deckColors'])}]")
                            
                            # Use color identity for deck colors if it's a commander format
                            identity = info.get("color_identity", [])
                            if not identity:
                                name_info = get_card_info_by_name(commander_name)
                                identity = name_info.get("color_identity", [])
                                
                            if identity:
                                self.tracker.current_match["deckColors"] = sorted(identity)
                                self.tracker.last_deck_colors = self.tracker.current_match["deckColors"]
                    
                    colors = self.tracker.extract_deck_colors(deck_data)
                    if colors and not self.tracker.current_match["deckColors"]:
                        self.tracker.current_match["deckColors"] = colors
                
                if is_target:
                    break

        # If called strictly, data should have CourseDeckSummary at top level
        summary = data.get("CourseDeckSummary")
        if not summary:
            # Fallback for other contexts
            summary = self.tracker.find_val(data, "CourseDeckSummary")
            
        if summary:
            name = summary.get("Name")
            self.tracker.update_deck_name(name)

    def handle_deck_summaries(self, data):
        """Handle DeckGetDeckSummariesV2 messages to populate card cache and deck names"""
        summaries = self.tracker.find_val(data, "Summaries")
        if summaries and isinstance(summaries, list):
            for deck in summaries:
                name = deck.get("Name")
                deck_id = deck.get("DeckId")
                # We can't do much with just the summary for card names, 
                # but it helps identify the deck.
                if name:
                    self.tracker.log(f"[DEBUG] Found deck summary: {name} ({deck_id})")

    def handle_deck_details(self, data):
        """Handle DeckGetDeckDetailsV2 messages - EXCELLENT for card names!"""
        deck = self.tracker.find_val(data, "deck")
        if not deck: return
        
        main_deck = deck.get("mainDeck", [])
        for card in main_deck:
            grp_id = card.get("grpId")
            card_name = card.get("cardName")
            if grp_id and card_name:
                CARD_CACHE[grp_id] = {
                    "id": grp_id,
                    "name": card_name
                }
        
        # Also check CommandZone in details
        command_zone = deck.get("commandZone", [])
        if command_zone:
            first = command_zone[0]
            grp_id = first.get("grpId") if isinstance(first, dict) else first
            if grp_id:
                info = get_card_info(grp_id)
                commander_name = info.get("name", f"Card#{grp_id}")
                self.tracker.current_match["heroCommander"] = commander_name
                
                if info.get("color_identity"):
                    self.tracker.current_match["deckColors"] = sorted(info["color_identity"])
                    self.tracker.last_deck_colors = self.tracker.current_match["deckColors"]
        
        save_card_cache()
        self.tracker.log(f"[INFO] Updated card cache with {len(main_deck)} names from deck details.")


class MTGATracker:
    def log(self, message, force=False):
        """Conditional logging based on quiet mode"""
        if self.quiet_mode and not force:
            return
        print(message, flush=True)

    def __init__(self):
        self.quiet_mode = False
        # Load persistent state
        state = self.load_state()
        self.match_history = state.get("matches", [])
        self.hero_identity = state.get("hero_identity", {"playerId": None, "screenName": None})
        
        # Load card cache
        load_card_cache()
        
        # Session stats
        self.session_stats = {"games_played": 0, "wins": 0, "losses": 0}
        
        self.current_log_time = 0
        self.last_waybar_refresh = 0
        
        # DYNAMIC: Start every session with no assumptions. 
        # The log must prove what we are playing.
        self.last_deck_name = "Unknown"
        self.last_deck_colors = []
        
        # Enhanced JSON handling and log entry handler
        self.json_buffer = JSONBuffer()
        self.log_handler = LogEntryHandler(self)
        
        # Enhanced match state
        self.reset_current_match()
        
        # Restore last used deck from history if available
        if self.match_history:
            last_match = self.match_history[-1]
            last_name = last_match.get("deck_name", "Unknown")
            if last_name != "Anvil Mid Range":
                self.last_deck_name = last_name
                self.last_deck_colors = last_match.get("deck_colors", [])
        
        # We start fresh - wait for log to tell us the deck
        self.current_match["deckName"] = "Unknown"
        self.current_match["deckColors"] = []
        self.last_deck_colors = []
        
        # Tracking state
        self.processed_matches = set()
        self.match_start_printed = False
        self.last_game_end_time = state.get("last_game_end_time", 0)
        self.last_match_result = None  # Stores 'win' or 'loss' for the end-game message
        
        # If tracker starts long after game ended, don't show the message
        if time.time() - self.last_game_end_time > 30:
             self.last_match_result = None
             
        self.recording_stats = True
        self.processed_transactions = set()
        self.processed_msg_ids = set()
        self.instance_to_grp = {} # Mapping of instanceId -> grpId for the current match

    def scan_for_active_match(self, log_path):
        """Quickly scan the end of the log to see if we are already in a match."""
        if not os.path.exists(log_path):
            return

        self.log("[INFO] Scanning for active match...")
        self.current_match["deckColors"] = []
        self.last_deck_colors = []
        try:
            with open(log_path, 'r', errors='ignore') as f:
                # Read last 150KB for context
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 150000))
                lines = f.readlines()
                
                # Look for the most recent match start markers
                # We process them in order to find the LATEST active match state
                for line in lines:
                    # Only process lines that are highly likely to be match setup
                    if any(kw in line for kw in ["MatchCreated", "ConnectResp", "DeckUpsertDeckV2", "EventSetDeckV2"]):
                        self.process_line(line)

            # If we didn't find a match but we have a last known deck, use it as fallback
            if not self.current_match["active"] and self.last_deck_name != "Unknown":
                # Only use fallback if it's not generic
                if not self.is_generic_name(self.last_deck_name):
                    self.current_match["deckName"] = self.last_deck_name

            if self.current_match["active"]:
                # Check for staleness here too
                if time.time() - self.current_log_time > 600:
                     self.current_match["active"] = False
                else:
                     self.log(f"[INFO] Re-connected to active match! Deck: {self.current_match['deckName']}")
            
            self.write_waybar_json()
        except Exception as e:
            self.log(f"[WARNING] Active match scan failed: {e}")
        
        # FIX 3: Add deduplication tracking
        self.processed_transactions = set()
        self.processed_msg_ids = set()
        
        # Game variant tracking
        self.current_game_variant = None
        
        # Deck tracking
        self.current_deck_info = None
        
        self.write_waybar_json()

    def load_state(self):
        """Loads match history and identity from JSON file."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.log(f"[INFO] Loaded {len(state.get('matches', []))} previous matches from {STATE_FILE}")
                    return state
            except Exception as e:
                self.log(f"[WARNING] Could not load state: {e}")
        return {"matches": [], "hero_identity": {"playerId": None, "screenName": None}}

    def save_state(self):
        """Saves match history and hero identity."""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump({
                    "matches": self.match_history,
                    "hero_identity": self.hero_identity,
                    "last_game_end_time": self.last_game_end_time,
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)
            self.write_waybar_json()
            # Update HTML stats
            try:
            # Run HTML generator to update the stats page
            try:
                generator_path = config.BASE_PATH / "html_generator.py"
                os.system(f"python3 {generator_path}")
            except Exception as e:
                log(f"[WARNING] Could not update HTML stats: {e}")
            except:
                pass
            self.log(f"[INFO] State saved ({len(self.match_history)} matches).")
        except Exception as e:
            self.log(f"[ERROR] Error saving state: {e}")

    def reset_stats(self, signum=None, frame=None):
        """Resets all stats."""
        self.log("[INFO] Resetting statistics to zero...", force=True)
        self.match_history = []
        self.processed_matches = set()
        self.session_stats = {"games_played": 0, "wins": 0, "losses": 0}
        self.save_state()
        self.log("[INFO] Stats reset complete.", force=True)

    def reset_current_match(self):
        """Resets the current match state to defaults for a new game."""
        self.current_game_variant = None
        self.current_match = {
            "matchId": None,
            "seatId": None,
            "teamId": None,
            "opponentName": "Unknown",
            "deckName": self.last_deck_name,
            "deckColors": [],
            "opponentColors": [],
            "eventId": None,
            "format": "Unknown",
            "startTime": self.current_log_time if (hasattr(self, 'current_log_time') and self.current_log_time > 0) else 0,
            "endTime": 0,
            "active": False,
            "mulligans": 0,
            "opponentMulligans": 0,
            "openingHandSize": 0,
            "maxTurns": 0,
            "rank": {"class": None, "tier": None, "step": None},
            "rankChange": None,
            "goingFirst": None,
            "winCondition": None,
            "totalCards": 0,
            "cardsDrawn": 0,
            "lifeTotals": {},
            "cardsSeen": [],
            "opponentCardsSeen": [],
            "spellsCast": 0,
            "landsPlayed": 0,
            "opponentCommander": "Unknown",
            "heroCommander": "Unknown",
            "opponentCommanderId": None,
            "heroCommanderId": None,
            "last_mulligan_decision": None,
            "last_logged_opp_mulls": None,
        }
        self.match_start_printed = False
        self.instance_to_grp = {}
        self.processed_transactions = set()
        self.processed_msg_ids = set()
        # Note: self.last_deck_colors is preserved or handled by deck detection

    def is_generic_name(self, name):
        """Checks if a deck name is generic or unknown."""
        if not name: return True
        generic_names = [
            "Unknown", "Default Deck", "Anvil Mid Range", "New Deck", 
            "Imported Deck", "Standard Deck", "Brawl Deck", "Unknown Card"
        ]
        if name in generic_names: return True
        if "Brawl: Card#" in name: return True
        if "Unknown Card" in name: return True
        if name.startswith("Card#"): return True
        return False

    def update_deck_name(self, name):
        """Safely updates the deck name, avoiding overwriting real names with generic ones."""
        if not self.recording_stats and not self.current_match.get("active"):
            # During initial scan, we still want to update last known deck
            pass
            
        if not name or name == "Unknown":
            return
            
        current = self.current_match.get("deckName", "Unknown")
        
        # If we have a hero commander, and the name is generic, format as Brawl: [Commander]
        hero_comm = self.current_match.get("heroCommander")
        if hero_comm and self.is_generic_name(name):
            name = f"Brawl: {hero_comm}"

        # If the new name is generic and we already have a better one, skip
        if self.is_generic_name(name) and not self.is_generic_name(current):
            return

        if name != current:
            if self.current_match.get("active") and not self.match_start_printed:
                self.log("\n🚀 Game Starting...")
            self.log(f"[INFO] Deck Identified: {name}")
            self.current_match["deckName"] = name
            self.last_deck_name = name
            self.write_waybar_json()
            if self.current_match.get("active"):
                self.save_state()

    def find_val(self, obj, key):
        """Recursively find a value for a key in a nested dictionary/list."""
        if isinstance(obj, dict):
            if key in obj: return obj[key]
            for v in obj.values():
                if isinstance(v, str) and len(v) > 2 and (v.startswith('{') or v.startswith('[')):
                    try:
                        nested = json.loads(v)
                        res = self.find_val(nested, key)
                        if res is not None: return res
                    except:
                        pass
                res = self.find_val(v, key)
                if res is not None: return res
        elif isinstance(obj, list):
            for v in obj:
                res = self.find_val(v, key)
                if res is not None: return res
        return None

    def get_season_start(self):
        """Returns the timestamp of the most recent set release."""
        now_str = datetime.now().strftime("%Y-%m-%d")
        last_release = "2020-01-01"
        
        for code, date_str in SET_RELEASES:
            if date_str <= now_str:
                last_release = date_str
            else:
                break
        
        dt = datetime.strptime(last_release, "%Y-%m-%d")
        return dt.timestamp()

    def calculate_stats(self, start_ts=0):
        """Calculates wins/losses from history since start_ts."""
        w, l = 0, 0
        for m in self.match_history:
            try:
                ts = m.get("timestamp", 0)
                if isinstance(ts, str):
                    # Try to parse ISO format or direct float
                    if 'T' in ts:
                        ts = datetime.fromisoformat(ts).timestamp()
                    else:
                        ts = float(ts)
                
                if ts >= start_ts:
                    if m.get("result") == "win": w += 1
                    elif m.get("result") == "loss": l += 1
            except (ValueError, TypeError, Exception):
                continue
        return w, l

    def get_deck_stats(self, deck_name):
        """Get stats for a specific deck."""
        w, l = 0, 0
        for m in self.match_history:
            if m.get("deck_name") == deck_name:
                if m.get("result") == "win": w += 1
                elif m.get("result") == "loss": l += 1
        return w, l

    def write_waybar_json(self):
        """Writes stats to a JSON file for Waybar with dynamic messages."""
        now = datetime.now()
        now_ts = now.timestamp()
        today_ts = datetime.combine(now.date(), datetime.min.time()).timestamp()
        
        # Color mapping for Mana font symbols and colors
        # Official Cheatsheet: W=e600, U=e601, B=e602, R=e603, G=e604, C=e904
        COLOR_MAP_DATA = {
            'W': ('\ue600', '#f8f1d1'), # White
            'U': ('\ue601', '#1ca3ec'), # Blue
            'B': ('\ue602', '#bababa'), # Black (Grey for visibility)
            'R': ('\ue603', '#fb4d42'), # Red
            'G': ('\ue604', '#1d9145'), # Green
            'C': ('\ue904', '#bababa'), # Colorless (ms-c)
        }
        
        deck_colors = self.current_match.get("deckColors", [])
        if not self.current_match.get("active"):
            # LOBBY: Use ms-dfc-ignite (\ue908)
            ICON_SPAN = "<span font='Mana' size='140%' foreground='#fb4d42'>\ue908</span>"
        else:
            # ACTIVE MATCH: Build dynamic icon string for deck colors
            display_colors = deck_colors
            
            # If no deck colors, try to get them from commander name lookup
            if not display_colors:
                hero_comm = self.current_match.get("heroCommander", "Unknown")
                if hero_comm != "Unknown":
                    info = get_card_info_by_name(hero_comm)
                    display_colors = info.get("color_identity", [])
            
            icons = []
            for c in display_colors:
                if c in COLOR_MAP_DATA:
                    sym, hex_color = COLOR_MAP_DATA[c]
                    icons.append(f"<span font='Mana' size='140%' foreground='{hex_color}'>{sym}</span>")
            
            if not icons:
                 # If we are in a match but have no colors, show the Lobby icon to indicate we are determining them
                 ICON_SPAN = "<span font='Mana' size='140%' foreground='#fb4d42'>\ue908</span>"
            else:
                 ICON_SPAN = "".join(icons)

        # Calculate these early so they are available for the tooltip
        w_today, l_today = self.calculate_stats(today_ts)
        w_total, l_total = self.calculate_stats(0)
        
        # 1. Deck Stats Calculation
        deck_name = self.current_match.get("deckName", "Unknown")
        d_wins, d_losses = 0, 0
        for m in self.match_history:
            if m.get("deck_name") == deck_name:
                if m.get("result") == "win": d_wins += 1
                else: d_losses += 1

        def fmt_rate(w, l):
            total = w + l
            rate = (w / total * 100) if total > 0 else 0
            return f"{w}W-{l}L ({rate:.0f}%)"

        # 2. Logic for Display Message
        status_class = "waiting"
        icon_rise = "-3000"
        text_rise = "000"
        
        def wrap_msg(icon, text):
            return f"<span rise='{icon_rise}'>{icon}</span> <span rise='{text_rise}'>{text}</span>"

        # Check if we should show the end-game message (for 3 seconds)
        if self.last_match_result and (now_ts - self.last_game_end_time < 3):
            if self.last_match_result == "win":
                msg = "Victory!"
                status_class = "win"
            else:
                msg = "Defeat"
                status_class = "loss"
            main_text = wrap_msg(ICON_SPAN, msg)
        elif self.current_match["active"]:
            if not deck_colors:
                # If we have a commander name, show it even if colors are still missing
                hero_comm = self.current_match.get("heroCommander", "Unknown")
                if hero_comm != "Unknown":
                    main_text = wrap_msg(ICON_SPAN, f"Brawl: {hero_comm} [{fmt_rate(d_wins, d_losses)}]")
                else:
                    main_text = wrap_msg(ICON_SPAN, "Connecting to game...")
            else:
                main_text = wrap_msg(ICON_SPAN, f"{deck_name} [{fmt_rate(d_wins, d_losses)}]")
            status_class = "active"
        else:
            main_text = wrap_msg(ICON_SPAN, f"MTGArena | Today: {fmt_rate(w_today, l_today)}")
            status_class = "waiting"

        # 3. Build Rich Dynamic Tooltip
        # Color Palette (Matching Omarchy style)
        C_ORANGE = "#ff9800"
        C_GREEN = "#81c784"
        C_RED = "#e57373"
        C_BLUE = "#64b5f6"
        C_WHITE = "#f0f0f0"
        C_GREY = "#aaaaaa"
        
        TOOLTIP_WIDTH = 40
        header_hline = "━" * TOOLTIP_WIDTH
        section_hline = "─" * TOOLTIP_WIDTH
        footer_hline = "┈" * TOOLTIP_WIDTH

        tooltip_lines = []
        # Header
        tooltip_lines.append(f"<span foreground='{C_ORANGE}'>󰐗 <b>MTGA PRO TRACKER</b></span>")
        tooltip_lines.append(f"<span foreground='{C_ORANGE}'>{header_hline}</span>")
        
        if self.current_match["active"] or (self.last_match_result and (now_ts - self.last_game_end_time < 15)):
            # Match Status Section
            match_status = "ACTIVE MATCH" if self.current_match["active"] else "RECENT MATCH"
            tooltip_lines.append(f"<span foreground='{C_BLUE}'>󰓅 <b>{match_status}</b></span>")
            
            # Format and Turns
            fmt = self.current_match.get('format', 'Unknown')
            round_info = ""
            if self.current_match.get('maxTurns', 0) > 0:
                round_num = (self.current_match['maxTurns'] + 1) // 2
                round_info = f" | <span foreground='{C_WHITE}'>Round {round_num} (Turn {self.current_match['maxTurns']})</span>"
            
            tooltip_lines.append(f"  <span foreground='{C_GREY}'>Format:</span> <span foreground='{C_WHITE}'>{fmt}</span>{round_info}")
            
            # Player / Opponent Line
            opp_name = self.current_match.get('opponentName', 'Unknown')
            hero_life = self.current_match.get('lifeTotals', {}).get(self.current_match.get('seatId'), 25)
            # Find opponent seat ID
            opp_seat = next((s for s in self.current_match.get('lifeTotals', {}) if s != self.current_match.get('seatId')), None)
            opp_life = self.current_match.get('lifeTotals', {}).get(opp_seat, 25)
            
            # Mana pips for hero and opponent
            hero_pips = "".join([f"<span foreground='{COLOR_MAP_DATA[c][1]}'>{COLOR_MAP_DATA[c][0]}</span>" for c in self.current_match.get('deckColors', []) if c in COLOR_MAP_DATA])
            opp_pips = "".join([f"<span foreground='{COLOR_MAP_DATA[c][1]}'>{COLOR_MAP_DATA[c][0]}</span>" for c in self.current_match.get('opponentColors', []) if c in COLOR_MAP_DATA])
            
            tooltip_lines.append("")
            tooltip_lines.append(f"  <span font='Mana' size='120%'>{hero_pips}</span> <span foreground='{C_WHITE}'><b>You</b> ({hero_life} ❤️)</span>")
            
            hero_comm = self.current_match.get('heroCommander')
            if hero_comm != "Unknown":
                tooltip_lines.append(f"    <span foreground='{C_GREY}'>󰚌 {hero_comm}</span>")
                
            tooltip_lines.append(f"  <span foreground='{C_GREY}'>vs</span>")
            tooltip_lines.append(f"  <span font='Mana' size='120%'>{opp_pips}</span> <span foreground='{C_WHITE}'><b>{opp_name}</b> ({opp_life} ❤️)</span>")
            
            opp_comm = self.current_match.get('opponentCommander')
            if opp_comm != "Unknown":
                tooltip_lines.append(f"    <span foreground='{C_GREY}'>󰚌 {opp_comm}</span>")

            # Mulligan info
            if self.current_match.get('mulligans', 0) > 0 or self.current_match.get('opponentMulligans', 0) > 0:
                tooltip_lines.append("")
                tooltip_lines.append(f"  <span foreground='{C_GREY}'>Mulligans:</span> <span foreground='{C_WHITE}'>You {self.current_match['mulligans']} - Opp {self.current_match['opponentMulligans']}</span>")

            tooltip_lines.append("")
            tooltip_lines.append(f"<span foreground='{C_GREY}'>{section_hline}</span>")

        # Stats Section
        tooltip_lines.append(f"<span foreground='{C_BLUE}'>󰏫 <b>STATISTICS</b></span>")
        
        def fmt_stat_row(label, wins, losses):
            total = wins + losses
            rate = (wins / total * 100) if total > 0 else 0
            color = C_GREEN if rate >= 50 else C_RED
            return f"  <span foreground='{C_GREY}'>{label:<12}</span> <span foreground='{C_WHITE}'>{wins}W-{losses}L</span> (<span foreground='{color}'>{rate:.0f}%</span>)"

        tooltip_lines.append(fmt_stat_row("Current Deck", d_wins, d_losses))
        tooltip_lines.append(fmt_stat_row("Today", w_today, l_today))
        tooltip_lines.append(fmt_stat_row("Session", self.session_stats['wins'], self.session_stats['losses']))
        tooltip_lines.append(fmt_stat_row("All-Time", w_total, l_total))

        # Footer
        tooltip_lines.append("")
        tooltip_lines.append(f"<span foreground='{C_GREY}'>{footer_hline}</span>")
        footer_text = "󰍽 LMB: Open Dashboard"
        footer_padding = max(0, (TOOLTIP_WIDTH - len(footer_text)) // 2)
        tooltip_lines.append(f"<span font_family='monospace' foreground='{C_GREY}'>{' ' * footer_padding}{footer_text}</span>")

        # Wrap everything in a size tag for better readability
        tooltip = "\n".join(tooltip_lines)
        tooltip = f"<span size='13000'>{tooltip}</span>"

        output = {
            "text": main_text,
            "tooltip": tooltip,
            "class": status_class,
            "markup": "pango",
            "alt": "active" if self.current_match["active"] else "waiting"
        }
        try:
            path = config.WAYBAR_JSON_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(output, f)
        except Exception:
            pass

    def extract_deck_colors(self, deck_data):
        """Extract color identity from deck data."""
        colors = set()
        if not deck_data:
            return []
        
        main_deck = deck_data.get("mainDeck", []) or deck_data.get("MainDeck", [])
        
        for card in main_deck:
            grp_id = card.get("grpId") or card.get("cardId")
            if grp_id:
                info = get_card_info(grp_id)
                # Use color_identity for Brawl/Commander, or just colors for Standard
                # Most robust is to use color_identity for the whole deck
                for c in info.get("color_identity", []):
                    colors.add(c)
                
                # Fallback to direct colors if identity is missing
                if not info.get("color_identity"):
                    for c in info.get("colors", []):
                        colors.add(c)

            # Fallback for log-provided color data
            card_colors = card.get("colors", []) or card.get("color", [])
            for color_id in card_colors:
                if isinstance(color_id, int) and color_id in COLOR_MAP:
                    colors.add(COLOR_MAP[color_id])
                elif isinstance(color_id, str) and color_id in STR_COLOR_MAP:
                    colors.add(STR_COLOR_MAP[color_id])
        
        # Check CommandZone too
        command_zone = deck_data.get("CommandZone", []) or deck_data.get("commandZone", [])
        for card in command_zone:
            grp_id = card.get("cardId") or card.get("grpId") if isinstance(card, dict) else card
            if grp_id:
                info = get_card_info(grp_id)
                for c in info.get("color_identity", []):
                    colors.add(c)

        return sorted(list(colors))

    def format_colors(self, colors):
        """Format color list into readable string."""
        if not colors:
            return "Colorless"
        if len(colors) == 1:
            color_names = {'W': 'White', 'U': 'Blue', 'B': 'Black', 'R': 'Red', 'G': 'Green'}
            return color_names.get(colors[0], colors[0])
        elif len(colors) == 2:
            return f"{''.join(colors)}"
        else:
            return f"{''.join(colors)} (Multicolor)"

    def extract_timestamp(self, line: str) -> float:
        """Extract timestamp from log line"""
        # MTGA uses format: "1/29/2026 11:38:25 PM"
        ts_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} (?:AM|PM))", line)
        if ts_match:
            try:
                return datetime.strptime(ts_match.group(1), "%m/%d/%Y %I:%M:%S %p").timestamp()
            except ValueError:
                pass
        
        # Fallback to ISO format
        ts_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if ts_match:
            try:
                return datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                pass
        
        return None

    def process_line(self, line):
        """Process a single log line - improved version"""
        # 1. Text-based Identity Detection (Fast)
        if "Display Name:" in line:
            dn_match = re.search(r"Display Name: ([^#\s]+#[0-9]+)", line)
            if dn_match:
                new_name = dn_match.group(1)
                if self.hero_identity["screenName"] != new_name:
                    self.hero_identity["screenName"] = new_name
                    self.log(f"[INFO] Identity confirmed: {new_name}")

        # 2. Extract timestamp and Event Name
        ts = self.extract_timestamp(line)
        if ts:
            self.current_log_time = ts
        
        # Extract event name from prefix like [UnityCrossThreadLogger]==> EventSetDeckV2
        event_name = None
        prefix_match = re.search(r"\[UnityCrossThreadLogger\](?:==>|<==)?\s*([a-zA-Z0-9._]+)", line)
        if prefix_match:
            event_name = prefix_match.group(1)
        else:
            # Fallback for lines without the logger prefix but with the arrow notation
            arrow_match = re.search(r"(?:==>|<==)\s*([a-zA-Z0-9._]+)", line)
            if arrow_match:
                event_name = arrow_match.group(1)

        # 3. JSON processing
        json_data = self.json_buffer.add_line(line)
        if json_data:
            self.log_handler.process(json_data, self.current_log_time, event_name)
            
        # 4. Periodic waybar heartbeat (every 15s)
        if time.time() - self.last_waybar_refresh > 15:
            self.write_waybar_json()
            self.last_waybar_refresh = time.time()

    def handle_client_message(self, msg):
        """Handles actions performed by the user (Client -> GRE)."""
        msg_type = msg.get("type")
        payload = msg.get("payload", {})
        
        if msg_type == "ClientMessageType_PerformAction":
            action = payload.get("performAction", {})
            # Spells and Lands tracking disabled for now
            # if "cast" in action:
            #     grp_id = action["cast"].get("grpId")
            #     card_name = get_card_name(grp_id)
            #     self.current_match["spellsCast"] += 1
            #     self.log(f"⚡ Cast: {card_name}")
            # elif "playLand" in action:
            #     grp_id = action["playLand"].get("grpId")
            #     card_name = get_card_name(grp_id)
            #     self.current_match["landsPlayed"] += 1
            #     self.log(f"🏔️  Land: {card_name}")
            if "activate" in action:
                self.log(f"⚙️  Activated Ability")
            elif "mulligan" in action:
                self.current_match["mulligans"] += 1
                self.log(f"[INFO] You took a Mulligan (Total: {self.current_match['mulligans']})")
            elif "concede" in action:
                self.log(f"[INFO] You conceded the game")

    def update_match_format(self, game_info=None):
        """Updates the match format based on eventId or variant."""
        if game_info:
            variant = game_info.get("variant")
            if variant == "GameVariant_Brawl":
                self.current_match["format"] = "Brawl"
                return
            elif variant == "GameVariant_Standard":
                self.current_match["format"] = "Standard"
                return
            elif variant == "GameVariant_Historic":
                self.current_match["format"] = "Historic"
                return

        # Fallback to variant tracking
        if hasattr(self, 'current_game_variant') and self.current_game_variant:
            variant = self.current_game_variant
            if variant == "GameVariant_Brawl":
                self.current_match["format"] = "Brawl"
                return
            elif variant == "GameVariant_Standard":
                self.current_match["format"] = "Standard"
                return
            elif variant == "GameVariant_Historic":
                self.current_match["format"] = "Historic"
                return
            elif variant == "GameVariant_Traditional":
                self.current_match["format"] = "Traditional"
                return
        
        event_id = self.current_match.get("eventId", "")
        if not event_id or event_id == "Unknown":
            return

        eid = event_id.replace("_", " ")
        
        if "Draft" in eid:
            self.current_match["format"] = "Draft"
        elif "Sealed" in eid:
            self.current_match["format"] = "Sealed"
        elif "Cube" in eid:
            self.current_match["format"] = "Cube"
        elif "JumpIn" in eid or "Jump In" in eid:
            self.current_match["format"] = "Jump In"
        elif "StarterDeck" in eid or "Starter Deck" in eid:
            self.current_match["format"] = "Starter Deck"
        elif "Brawl" in eid or "Commander" in eid or "Friendly Brawl" in eid or "AIBotMatch" in eid:
            if "Historic" in eid:
                self.current_match["format"] = "Historic Brawl"
            elif "Standard" in eid:
                self.current_match["format"] = "Standard Brawl"
            elif "AIBotMatch" in eid:
                # If bot match, it might be brawl if we see brawl indicators elsewhere, 
                # but usually it's just "Bot Match" unless specified.
                if self.current_match["format"] == "Unknown":
                    self.current_match["format"] = "Bot Match"
            else:
                self.current_match["format"] = "Brawl"
        elif "Timeless" in eid:
            self.current_match["format"] = "Timeless"
        elif "Historic" in eid:
            self.current_match["format"] = "Historic"
        elif "Explorer" in eid:
            self.current_match["format"] = "Explorer"
        elif "Alchemy" in eid:
            self.current_match["format"] = "Alchemy"
        elif "Standard" in eid:
            self.current_match["format"] = "Standard"
        elif "Pauper" in eid:
            self.current_match["format"] = "Pauper"
        elif "Artisan" in eid:
            self.current_match["format"] = "Artisan"
        elif "Momir" in eid:
            self.current_match["format"] = "Momir"
        elif "Gladiator" in eid:
            self.current_match["format"] = "Gladiator"
        elif "MidWeekMagic" in eid or "MWM" in eid:
            self.current_match["format"] = "MidWeek Magic"
        elif "Festival" in eid:
            self.current_match["format"] = "Festival"
        elif "Practice" in eid or "Sparky" in eid:
            self.current_match["format"] = "Bot Match"
        
        # Fallback for unknown formats
        if self.current_match["format"] == "Unknown" and eid:
            self.current_match["format"] = eid

    def format_event_name(self, event_id):
        """Converts internal event IDs to human readable text."""
        if not event_id: return "Unknown Event"
        name = event_id.replace("Play_", "").replace("Ranked_", "Ranked ").replace("Constructed_", "")
        name = name.replace("Bo1", "BO1").replace("Bo3", "BO3")
        name = name.replace("_", " ")
        return name.strip()

    def print_match_start(self):
        """Prints the match start message once per match."""
        event_name = self.format_event_name(self.current_match["eventId"])
        format_name = self.current_match["format"]
        
        # Avoid redundancy (e.g. "Brawl (Brawl)")
        if event_name == format_name or event_name == "Unknown Event":
            display_format = format_name
        else:
            display_format = f"{format_name} ({event_name})"
        
        my_c = "".join(self.current_match["deckColors"])
        opp_c = "".join(self.current_match["opponentColors"])
        
        my_color_str = f" [{my_c}]" if my_c else ""
        opp_color_str = f" [{opp_c}]" if opp_c else ""
        
        start_time_str = datetime.fromtimestamp(self.current_match["startTime"]).strftime('%I:%M %p')
        opponent_info = f"{self.current_match['opponentName']}"
        opp_commander = self.current_match.get("opponentCommander", "Unknown")
        
        self.log("\n" + "═"*60)
        self.log(f"⚔️  MATCH STARTED: {start_time_str}")
        if display_format != "Unknown":
            self.log(f"📋 Format:   {display_format}")
        self.log(f"👤 Players:  {self.hero_identity.get('screenName', 'You')}{my_color_str} vs {opponent_info}{opp_color_str}")
        if opp_commander != "Unknown":
            self.log(f"👤 Opponent's Commander: {opp_commander}")
        
        if self.current_match["goingFirst"] is not None:
            turn_pos = "On the Play" if self.current_match["goingFirst"] else "On the Draw"
            self.log(f"🎲 Turn:     {turn_pos}")
        
        self.log("═"*60 + "\n")
        self.match_start_printed = True

    def handle_gre_message(self, msg):
        """Handle GRE messages - with recursive unwrapping of bundled messages"""
        if not msg: return

        # 1. Recursive Unwrapping: GRE often bundles messages in greToClientMessages
        if "greToClientEvent" in msg:
            gre_event = msg.get("greToClientEvent", {})
            for sub_msg in gre_event.get("greToClientMessages", []):
                # Inherit transactionId for the sub-message if it's missing
                if "transactionId" not in sub_msg and "transactionId" in msg:
                    sub_msg["transactionId"] = msg["transactionId"]
                # Inherit systemSeatIds for the sub-message if it's missing
                if "systemSeatIds" not in sub_msg and "systemSeatIds" in msg:
                    sub_msg["systemSeatIds"] = msg["systemSeatIds"]
                self.handle_gre_message(sub_msg)
            return

        # 2. Deduplication based on msgId only
        msg_id = msg.get("msgId")
        if msg_id and msg_id in self.processed_msg_ids:
            return  # Skip duplicate
        if msg_id:
            self.processed_msg_ids.add(msg_id)
            # Keep only last 1000
            if len(self.processed_msg_ids) > 1000:
                self.processed_msg_ids = set(list(self.processed_msg_ids)[-500:])
        
        msg_type = msg.get("type", "")

        if not msg_type:
            if "matchCreated" in msg or "MatchCreated" in msg:
                msg_type = "MatchCreated"
            elif "matchGameRoomStateChangedEvent" in msg:
                msg_type = "MatchGameRoomStateChanged"
            elif "connectResp" in msg or "ConnectResp" in msg:
                msg_type = "ConnectResp"
            elif "mulliganReq" in msg or "MulliganReq" in msg:
                msg_type = "MulliganReq"
            elif "intermissionReq" in msg or "IntermissionReq" in msg:
                msg_type = "IntermissionReq"
            elif "gameStateMessage" in msg or "GameStateMessage" in msg:
                msg_type = "GameStateMessage"
            elif "actionsAvailableReq" in msg:
                msg_type = "ActionsAvailableReq"
            elif "uiMessage" in msg:
                msg_type = "UIMessage"

        # Duration fallback: initialize startTime if missing
        if self.current_match["startTime"] == 0 and self.current_log_time > 0:
            self.current_match["startTime"] = self.current_log_time

        # Seat ID detection
        if self.current_match["seatId"] is None:
            # Check for seat in headers
            seat_ids = msg.get("systemSeatIds", [])
            if seat_ids and len(seat_ids) == 1:
                # If message is specifically for one seat, it's usually our seat
                self.current_match["seatId"] = seat_ids[0]
            
            seat = self.find_val(msg, "systemSeatId")
            if seat is not None:
                self.current_match["seatId"] = seat
                if self.current_match["teamId"] is None:
                    self.current_match["teamId"] = seat
        
        # Identity reinforcement
        if self.hero_identity["screenName"] is None:
            screen_name = self.find_val(msg, "screenName")
            if screen_name:
                self.hero_identity["screenName"] = screen_name
                self.log(f"[INFO] Identity discovered from log: {screen_name}")

        # Connect Response
        if "ConnectResp" in msg_type:
            connect_resp = msg.get("connectResp") or msg.get("ConnectResp") or msg
            if self.current_match["startTime"] == 0:
                self.current_match["startTime"] = self.current_log_time
            
            msg_seats = msg.get("systemSeatIds", [])
            if "systemSeatId" in connect_resp:
                self.current_match["seatId"] = connect_resp.get("systemSeatId")
                self.current_match["active"] = True
                
                # Detect who goes first
                starting_team = connect_resp.get("settings", {}).get("startingTeamId")
                if starting_team is not None:
                    self.current_match["goingFirst"] = (starting_team == self.current_match["seatId"])

            # OPONENT DECK DETECTION (ConnectResp often has full deck lists!)
            deck_msg = connect_resp.get("deckMessage", {})
            if deck_msg:
                comm_cards = deck_msg.get("commanderCards", [])
                if comm_cards:
                    # If this message is specifically for our seat, the cards are ours
                    # If it's a broadcast or for the other seat, we need to be careful
                    for grp_id in comm_cards:
                        info = get_card_info(grp_id)
                        c_name = info.get("name", f"Card#{grp_id}")
                        
                        # Logic: Use Seat IDs from message header to assign commander
                        # ConnectResp with a single seat ID in the header is usually private deck info
                        is_my_message = (len(msg_seats) == 1 and msg_seats[0] == self.current_match["seatId"])
                        
                        if is_my_message:
                            # This is our deck info
                            self.current_match["heroCommanderId"] = grp_id
                            self.current_match["heroCommander"] = c_name
                        else:
                            # This is likely the opponent's deck info or a broadcast
                            # Fallback: if it's not our known commander name, it's the opponent's
                            # Avoid using seat 0 as it's a placeholder
                            opp_seat = 1 if self.current_match["seatId"] == 2 else 2
                            self._identify_commander(grp_id, opp_seat)



        # Match Created
        elif "MatchCreated" in msg_type:
            self.reset_current_match()
            match_created = msg.get("matchCreated") or msg.get("MatchCreated") or msg
            self.current_match["matchId"] = match_created.get("matchId")
            self.current_match["eventId"] = match_created.get("eventId", "Unknown")
            self.current_match["active"] = True
            self.update_match_format()
            
            # Fallback deck name from MatchCreated if found
            deck_summary = match_created.get("deckSummary", {})
            found_name = deck_summary.get("Name") or match_created.get("deckName")
            self.update_deck_name(found_name)
            
            self.write_waybar_json() # Force update on match start
            self.save_state()
            
            if self.current_match["startTime"] == 0:
                self.current_match["startTime"] = self.current_log_time
            
            teams = match_created.get("teams", [])
            for team in teams:
                for player in team.get("players", []):
                    is_me = False
                    if self.current_match["seatId"] is not None:
                        if player.get("systemSeatId") == self.current_match["seatId"]:
                            is_me = True
                    elif self.hero_identity["playerId"] is not None:
                        if player.get("userId") == self.hero_identity["playerId"]:
                            is_me = True
                            self.current_match["seatId"] = player.get("systemSeatId")

                    if is_me:
                        self.current_match["teamId"] = team.get("id")
                    else:
                        self.current_match["opponentName"] = player.get("playerName", "Unknown")
                        
                        # Try to find opponent commander in player deck summary
                        deck_summary = player.get("deckSummary", {})
                        if deck_summary:
                            comm_ids = deck_summary.get("commanderCards", [])
                            if comm_ids:
                                for grp_id in comm_ids:
                                    s_id = player.get("systemSeatId")
                                    if s_id is not None and s_id != 0:
                                        self._identify_commander(grp_id, s_id)

            if self.current_match["opponentName"] == "Sparky" and self.current_match["eventId"] == "Unknown":
                self.current_match["eventId"] = "Practice"
                self.update_match_format()

            if not self.match_start_printed:
                self.print_match_start()

        # Match Game Room State Changed - WITH FIX 1 APPLIED
        elif "MatchGameRoomStateChanged" in msg_type:
            room_event = msg.get("matchGameRoomStateChangedEvent", {})
            game_room_info = room_event.get("gameRoomInfo", {})
            game_room_config = game_room_info.get("gameRoomConfig", {})
            
            if "matchId" in room_event:
                self.current_match["matchId"] = room_event["matchId"]
            
            # FIX 1: Dynamic player seat detection using screenName
            reserved_players = game_room_config.get("reservedPlayers", [])
            for player in reserved_players:
                player_name = player.get("playerName", "Unknown")
                system_seat_id = player.get("systemSeatId")
                team_id = player.get("teamId")
                
                # Robust matching: Try exact, then try splitting at # for base name
                is_me = False
                my_full_name = self.hero_identity.get("screenName")
                if my_full_name:
                    my_base_name = my_full_name.split('#')[0]
                    if player_name == my_full_name or player_name == my_base_name:
                        is_me = True
                
                if is_me:
                    # This is the player
                    if system_seat_id is not None:
                        self.current_match["seatId"] = system_seat_id
                        self.current_match["teamId"] = team_id
                else:
                    # This is the opponent
                    self.current_match["opponentName"] = player_name
            
            event_id = (
                game_room_config.get("eventId") or 
                game_room_config.get("matchType") or
                game_room_info.get("gameRoomConfig", {}).get("eventId") or
                ""
            )
            
            if not event_id and self.current_match.get("opponentName") == "Sparky":
                event_id = "Practice"
            
            if not event_id:
                state_type = game_room_info.get("stateType", "")
                if state_type and "Playing" not in state_type and "MatchCompleted" not in state_type:
                    event_id = state_type
            
            if not event_id:
                event_id = "Unknown"
            
            if event_id != "Unknown":
                self.current_match["eventId"] = event_id
                self.update_match_format()
            
            if self.current_match["startTime"] == 0:
                self.current_match["startTime"] = self.current_log_time
            
            state_type = game_room_info.get("stateType", "")
            if not self.match_start_printed and self.current_match["opponentName"] != "Unknown" and "MatchCompleted" not in state_type:
                self.print_match_start()

        # Game State Message
        elif "GameStateMessage" in msg_type:
            game_state = msg.get("gameStateMessage") or msg
            game_objects = game_state.get("gameObjects", [])
            
            # 0. Track instance to GRP mapping for reliable lookup
            for obj in game_objects:
                i_id = obj.get("instanceId")
                g_id = obj.get("grpId")
                if i_id and g_id:
                    self.instance_to_grp[i_id] = g_id

            # 1. Track every card seen in game objects
            for obj in game_objects:
                obj_type = obj.get("type")
                if obj_type not in ["GameObjectType_Card", "GameObjectType_Token"]:
                    continue
                    
                grp_id = obj.get("grpId")
                if not grp_id: continue
                
                owner = obj.get("ownerSeatId")
                if owner is not None:
                    if owner == self.current_match["seatId"]:
                        if grp_id not in self.current_match["cardsSeen"]:
                            self.current_match["cardsSeen"].append(grp_id)
                    else:
                        if grp_id not in self.current_match["opponentCardsSeen"]:
                            self.current_match["opponentCardsSeen"].append(grp_id)

            # 2. Existing life total and commander logic...
            for obj in game_objects:
                if obj.get("type") == "GameObjectType_Player" or (obj.get("systemSeatId") is not None and "lifeTotal" in obj):
                    seat_id = obj.get("systemSeatId") or obj.get("controllerSeatId")
                    life = obj.get("lifeTotal")
                    
                    if seat_id is not None and life is not None:
                        prev_life = self.current_match["lifeTotals"].get(seat_id)
                        if prev_life is not None and prev_life != life:
                            player_name = "Opponent"
                            if seat_id == self.current_match["seatId"]:
                                player_name = "You"
                            
                            diff = life - prev_life
                            sign = "+" if diff > 0 else ""
                            glyph = "❤️" if diff > 0 else "💔"
                            self.log(f"{glyph} {player_name} Life: {prev_life} -> {life} ({sign}{diff})")
                        
                        self.current_match["lifeTotals"][seat_id] = life
                
            # 3. Process Annotations for Color Identity and Commander
            # Check both 'annotations' and 'persistentAnnotations'
            all_annos = game_state.get("annotations", []) + game_state.get("persistentAnnotations", [])
            for anno in all_annos:
                if "AnnotationType_Designation" in anno.get("type", []):
                    details = anno.get("details", [])
                    
                    # A. Color Identity
                    color_id_info = next((d for d in details if d.get("key") == "ColorIdentity"), None)
                    # B. Commander GRPID Identification
                    grp_id_info = next((d for d in details if d.get("key") == "grpid"), None)
                    
                    affected_ids = anno.get("affectedIds", [])
                    if affected_ids:
                        target_seat = affected_ids[0]
                        # Verify target_seat is a valid player seat (usually 1 or 2)
                        if target_seat in [1, 2, self.current_match.get("seatId")]:
                            is_hero = (target_seat == self.current_match["seatId"])
                            
                            # Handle Commander Identification
                            if grp_id_info:
                                val = grp_id_info.get("valueInt32", [])
                                if val:
                                    self._identify_commander(val[0], target_seat)

                            # Handle Color Identity
                            if color_id_info:
                                raw_colors = color_id_info.get("valueInt32", [])
                                mapped_colors = []
                                INT_COLOR_MAP = {1: 'W', 2: 'U', 3: 'B', 4: 'R', 5: 'G'}
                                for c_id in raw_colors:
                                    if c_id in INT_COLOR_MAP:
                                        mapped_colors.append(INT_COLOR_MAP[c_id])
                                
                                if mapped_colors:
                                    if is_hero:
                                        for c in mapped_colors:
                                            if c not in self.current_match["deckColors"]:
                                                self.current_match["deckColors"].append(c)
                                        self.current_match["deckColors"].sort()
                                        self.last_deck_colors = self.current_match["deckColors"]
                                    else:
                                        for c in mapped_colors:
                                            if c not in self.current_match["opponentColors"]:
                                                self.current_match["opponentColors"].append(c)
                                        self.current_match["opponentColors"].sort()
                                        color_str = "".join(self.current_match["opponentColors"])
                                        # self.log(f"[INFO] Opponent Color Identity Identified: {color_str}") # Redundant now

            # 4. Brawl specific: Command Zone check (Zone 26 and 29)
            # 4a. Check gameObjects
            for obj in game_objects:
                # ONLY if it's in a commander zone OR explicitly typed as a commander
                zone_id = obj.get("zoneId")
                is_commander_zone = zone_id in [26, 29]
                is_commander_type = obj.get("type") == "GameObjectType_Commander"
                
                if is_commander_zone or is_commander_type:
                    commander_owner = obj.get("ownerSeatId")
                    if commander_owner is not None:
                        grp_id = obj.get("grpId")
                        if not grp_id: continue
                        
                        # In Brawl, anything in Zone 26 belonging to a seat is likely the commander
                        is_brawl = self.current_match.get("format") == "Brawl" or self.current_match.get("variant") == "GameVariant_Brawl"
                        
                        # Verify it's actually a commander card before identifying
                        info = get_card_info(grp_id)
                        is_comm = info.get("is_commander")
                        if not is_comm and (info.get("is_legendary") or is_brawl):
                             # Fallback for legendary creatures/planeswalkers in commander zones
                             # OR anything in Zone 26 in a Brawl match
                             t_line = info.get("type_line", "")
                             if "Creature" in t_line or "Planeswalker" in t_line or is_brawl:
                                 is_comm = True
                        
                        if is_comm:
                            self._identify_commander(grp_id, commander_owner)

            # 4b. Check Actions (Commanders often show up here as castable first)
            actions = game_state.get("actions", [])
            for action_obj in actions:
                act = action_obj.get("action", {})
                if act.get("actionType") == "ActionType_Cast":
                    # Only check if it's coming from the Command Zone
                    # Action messages don't always show the zone, so we rely on _identify_commander's internal check
                    owner = action_obj.get("seatId")
                    grp_id = act.get("grpId") or self.instance_to_grp.get(act.get("instanceId")) or self.instance_to_grp.get(act.get("sourceId"))
                    if owner and grp_id:
                        # Only check if we don't already have a firm commander identification
                        # to prevent random legends from being identified as commanders mid-game
                        is_opp = (owner != self.current_match.get("seatId"))
                        curr_opp_comm = self.current_match.get("opponentCommander", "Unknown")
                        
                        if not is_opp or self.is_generic_name(curr_opp_comm):
                            info = get_card_info(grp_id)
                            is_comm = info.get("is_commander")
                            if not is_comm and info.get("is_legendary"):
                                 t_line = info.get("type_line", "")
                                 if "Creature" in t_line or "Planeswalker" in t_line:
                                     is_comm = True
                            
                            if is_comm:
                                self._identify_commander(grp_id, owner)

            # 4c. Check Actions Available (Prompt messages)
            avail = msg.get("actionsAvailableReq", {})
            for act in avail.get("actions", []):
                if act.get("actionType") == "ActionType_Cast":
                    owner = self.current_match["seatId"]
                    grp_id = act.get("grpId") or self.instance_to_grp.get(act.get("instanceId"))
                    if grp_id:
                        # For hero, we are usually more certain, but let's be safe
                        curr_hero_comm = self.current_match.get("heroCommander", "Unknown")
                        if self.is_generic_name(curr_hero_comm):
                            info = get_card_info(grp_id)
                            is_comm = info.get("is_commander")
                            if not is_comm and info.get("is_legendary"):
                                 t_line = info.get("type_line", "")
                                 if "Creature" in t_line or "Planeswalker" in t_line:
                                     is_comm = True
                            
                            if is_comm:
                                self._identify_commander(grp_id, owner)

            # 5. Track colors for both players (General)
            for obj in game_objects:
                obj_type = obj.get("type")
                if obj_type not in ["GameObjectType_Card", "GameObjectType_Token"]:
                    continue
                    
                owner_seat = obj.get("ownerSeatId")
                controller_seat = obj.get("controllerSeatId")
                
                # We care about both owner and controller for color identity 
                if (owner_seat is not None or controller_seat is not None) and self.current_match["seatId"] is not None:
                    target_seat = owner_seat if owner_seat is not None else controller_seat
                    found_colors = set()
                    
                    # Use card info for reliable colors
                    grp_id = obj.get("grpId")
                    if grp_id:
                        info = get_card_info(grp_id)
                        for c in info.get("color_identity", []):
                            found_colors.add(c)
                    
                    # 1. Direct color fields (fallback)
                    c_list = obj.get("color", []) or obj.get("colors", [])
                    for color_id in c_list:
                        if isinstance(color_id, int) and color_id in COLOR_MAP:
                            found_colors.add(COLOR_MAP[color_id])
                        elif isinstance(color_id, str) and color_id in STR_COLOR_MAP:
                            found_colors.add(STR_COLOR_MAP[color_id])
                    
                    # 2. Mana cost (pips)
                    mana_cost = obj.get("manaCost", "")
                    if mana_cost:
                        for symbol, color in [('w', 'W'), ('u', 'U'), ('b', 'B'), ('r', 'R'), ('g', 'G')]:
                            if f"o{symbol}" in mana_cost.lower():
                                found_colors.add(color)
                    
                    if target_seat == self.current_match["seatId"]:
                        # Your colors - BE CAREFUL with color creep
                        # If we have a commander, our color identity is FIXED.
                        if self.current_match["heroCommander"] != "Unknown":
                            comm_id = self.current_match.get("heroCommanderId")
                            if comm_id:
                                info = get_card_info(comm_id)
                                found_colors = set(info.get("color_identity", []))
                            else:
                                found_colors = set() # Fallback if ID missing
                        
                        new_color = False
                        for c in found_colors:
                            if c not in self.current_match["deckColors"]:
                                self.current_match["deckColors"].append(c)
                                self.current_match["deckColors"].sort()
                                new_color = True
                        
                        if new_color:
                            color_str = "".join(self.current_match["deckColors"])
                            self.last_deck_colors = self.current_match["deckColors"]
                            self.log(f"[INFO] Your Deck Colors: {color_str}")
                        
                        # Track cards seen in this deck
                        if grp_id and grp_id not in self.current_match["cardsSeen"]:
                            self.current_match["cardsSeen"].append(grp_id)
                    else:
                        # Opponent colors
                        new_color = False
                        for c in found_colors:
                            if c not in self.current_match["opponentColors"]:
                                self.current_match["opponentColors"].append(c)
                                self.current_match["opponentColors"].sort()
                                new_color = True
                        
                        if new_color:
                            color_str = "".join(self.current_match["opponentColors"])
                            self.log(f"[INFO] Opponent Deck Colors: {color_str}")
                        
                        # Track cards seen in this deck
                        if grp_id and grp_id not in self.current_match["opponentCardsSeen"]:
                            self.current_match["opponentCardsSeen"].append(grp_id)

            # 5. Commander Color Synchronization
            # Ensure deck colors include commander color identity
            for player_type in ["hero", "opponent"]:
                comm_name = self.current_match.get(f"{player_type}Commander")
                if not self.is_generic_name(comm_name):
                    info = get_card_info_by_name(comm_name)
                    identity = info.get("color_identity", [])
                    if identity:
                        target_colors = "deckColors" if player_type == "hero" else "opponentColors"
                        changed = False
                        for c in identity:
                            if c not in self.current_match[target_colors]:
                                self.current_match[target_colors].append(c)
                                changed = True
                        if changed:
                            self.current_match[target_colors].sort()
                            if player_type == "hero": self.last_deck_colors = self.current_match[target_colors]
                            color_str = "".join(self.current_match[target_colors])
                            self.log(f"[INFO] {player_type.capitalize()} Deck Colors Synced with Commander: {color_str}")

        # Mulligan Request
        elif "MulliganReq" in msg_type:
            mulligan_req = msg.get('mulliganReq') or msg.get('MulliganReq') or msg
            seat_id = mulligan_req.get("systemSeatId")
            count = mulligan_req.get("mulliganCount", 0)

            if seat_id == self.current_match["seatId"]:
                self.current_match["mulligans"] = count
                if count > 0:
                    self.log(f"[INFO] You are on Mulligan {count} (Hand Size: {7 - count})")
            elif seat_id is not None:
                # Track if we already logged this mulligan state for the opponent
                last_opp_mulls = self.current_match.get("last_logged_opp_mulls")
                if last_opp_mulls != count:
                    self.current_match["opponentMulligans"] = count
                    self.current_match["last_logged_opp_mulls"] = count
                    if count > 0:
                        self.log(f"[INFO] Opponent is on Mulligan {count}")
                    elif count == 0 and self.current_match["active"]:
                        self.log(f"[INFO] Opponent kept their hand")

        # Intermission
        elif "IntermissionReq" in msg_type:
            intermission = msg.get("intermissionReq") or msg.get("IntermissionReq") or msg
            prompt = intermission.get("prompt")
            
            if prompt and "MULLIGAN" in str(prompt):
                result = intermission.get("result", {})
                hand_cards = result.get("handCards", [])
                if hand_cards:
                    self.current_match["openingHandSize"] = len(hand_cards)
                    self.log(f"[INFO] Opening hand: {self.current_match['openingHandSize']} cards")

        # Turn Info
        turn_info = self.find_val(msg, "turnInfo")
        if turn_info:
            turn_number = turn_info.get("turnNumber", 0)
            
            if turn_number == 1 and self.current_match["goingFirst"] is None:
                active_player = turn_info.get("activePlayer")
                if active_player is not None and self.current_match["seatId"] is not None:
                     self.current_match["goingFirst"] = (active_player == self.current_match["seatId"])

            if turn_number > self.current_match["maxTurns"]:
                self.current_match["maxTurns"] = turn_number
                
                round_num = (turn_number + 1) // 2
                is_your_turn = (turn_number % 2 == 1)
                
                if self.current_match.get("goingFirst") is False:
                    is_your_turn = not is_your_turn
                
                whose_turn = "Your" if is_your_turn else "Opponent"
                
                if whose_turn == "Your":
                    suffix = "round"
                
                # Turn tracking updated internally, but log notification removed per user request
                pass

        # Game End - WITH FIX 2 APPLIED
        winning_team = self.find_val(msg, "winningTeamId")
        
        if winning_team is not None:
            if self.current_log_time - self.last_game_end_time < 20:
                return

            if not self.current_match["active"]:
                self.current_match["active"] = True
            
            if self.current_match["active"]:
                match_id = self.current_match.get("matchId")
                if match_id and match_id in self.processed_matches:
                    self.log(f"[INFO] Match {match_id} already processed. Skipping.")
                    self.current_match["active"] = False
                    return

                if match_id:
                    self.processed_matches.add(match_id)

                # FIX 2: Don't use seatId as fallback for teamId
                # Removed buggy code: if self.current_match["teamId"] is None and self.current_match["seatId"] is not None:
                #     self.current_match["teamId"] = self.current_match["seatId"]

                if self.recording_stats:
                    self.session_stats["games_played"] += 1
                    
                    self.current_match["endTime"] = self.current_log_time
                    duration_str = "N/A"
                    duration_seconds = 0
                    start_time = self.current_match.get("startTime", 0)
                    if start_time > 0:
                        duration_seconds = self.current_log_time - start_time
                        mins, secs = divmod(int(duration_seconds), 60)
                        duration_str = f"{mins}m {secs}s"

                    win_reason = self.find_val(msg, "winningReason")
                    if win_reason:
                        reason_map = {
                            "WinCondition_Game": "Game Win",
                            "WinCondition_Concede": "Opponent Conceded",
                            "WinCondition_Timeout": "Opponent Timeout"
                        }
                        self.current_match["winCondition"] = reason_map.get(win_reason, win_reason)

                    result = "unknown"
                    if self.current_match["teamId"] is not None:
                        if winning_team == self.current_match["teamId"]:
                            self.session_stats["wins"] += 1
                            result = "win"
                            self.last_match_result = "win"
                            
                            win_info = f"({duration_str}"
                            if self.current_match["maxTurns"] > 0:
                                round_num = (self.current_match['maxTurns'] + 1) // 2
                                win_info += f", Round {round_num}"
                            if self.current_match["winCondition"]:
                                win_info += f", {self.current_match['winCondition']}"
                            win_info += ")"
                            
                            opp_display = self.current_match['opponentName']
                            if self.current_match['opponentCommander']:
                                opp_display += f" ({self.current_match['opponentCommander']})"

                            self.log(f"\n🏆 VICTORY vs {opp_display}! {win_info}")
                        else:
                            self.session_stats["losses"] += 1
                            result = "loss"
                            self.last_match_result = "loss"
                            
                            loss_info = f"({duration_str}"
                            if self.current_match["maxTurns"] > 0:
                                round_num = (self.current_match['maxTurns'] + 1) // 2
                                loss_info += f", Round {round_num}"
                            loss_info += ")"
                            
                            opp_display = self.current_match['opponentName']
                            if self.current_match['opponentCommander']:
                                opp_display += f" ({self.current_match['opponentCommander']})"

                            self.log(f"\n💀 DEFEAT vs {opp_display} {loss_info}")
                        
                        match_record = {
                            "timestamp": self.current_log_time,
                            "date": datetime.fromtimestamp(self.current_log_time).strftime('%Y-%m-%d %H:%M:%S'),
                            "result": result,
                            "opponent": self.current_match["opponentName"],
                            "opponent_commander": self.current_match["opponentCommander"],
                            "hero_commander": self.current_match["heroCommander"],
                            "opponent_commander_id": self.current_match["opponentCommanderId"],
                            "hero_commander_id": self.current_match["heroCommanderId"],
                            "cards_seen": self.current_match["cardsSeen"].copy(),
                            "opponent_cards_seen": self.current_match["opponentCardsSeen"].copy(),
                            "opponent_colors": self.current_match["opponentColors"],
                            "deck_name": self.current_match["deckName"],
                            "deck_colors": self.current_match["deckColors"],
                            "event": self.current_match["eventId"],
                            "format": self.current_match["format"],
                            "duration_seconds": int(duration_seconds),
                            "turns": self.current_match["maxTurns"],
                            "mulligans": self.current_match["mulligans"],
                            "opponent_mulligans": self.current_match["opponentMulligans"],
                            "opening_hand_size": self.current_match["openingHandSize"],
                            "going_first": self.current_match["goingFirst"],
                            "win_condition": self.current_match["winCondition"],
                            "rank": self.current_match["rank"].copy() if self.current_match["rank"]["class"] else None,
                            "rank_change": self.current_match["rankChange"],
                            "match_id": self.current_match["matchId"],
                            "spells_cast": self.current_match["spellsCast"],
                            "lands_played": self.current_match["landsPlayed"],
                        }
                        
                        self.match_history.append(match_record)
                        
                    else:
                        self.log(f"\n[RESULT] Game Ended. Winner: Team {winning_team} (My Team Unknown)")
                    
                    self.log(f"Session: {self.session_stats['wins']}W - {self.session_stats['losses']}L ({self.session_stats['games_played']} Played)")
                    
                    # Final match summary
                    my_c = "".join(self.current_match["deckColors"])
                    opp_c = "".join(self.current_match["opponentColors"])
                    my_color_info = f"[{my_c}]" if my_c else ""
                    opp_color_info = f"[{opp_c}]" if opp_c else ""
                    
                    opp_display = self.current_match['opponentName']
                    if self.current_match['opponentCommander']:
                        opp_display += f" ({self.current_match['opponentCommander']})"
                    
                    self.log(f"📊 Summary:  {self.current_match['format']} | {self.current_match['deckName']}{my_color_info} vs {opp_display}{opp_color_info}")
                    
                    round_num = (self.current_match['maxTurns'] + 1) // 2
                    self.log(f"⏱️  Stats:    {duration_str} | {self.current_match['maxTurns']} Total Turns ({round_num} rounds per player)")
                    self.log(f"🃏 Mulligans: You {self.current_match['mulligans']} - Opponent {self.current_match['opponentMulligans']}")
                    # self.log(f"🪄  Actions:   {self.current_match['spellsCast']} Spells Cast | {self.current_match['landsPlayed']} Lands Played")
                    
                    self.log("")
                    
                    self.save_state()
                    self.last_game_end_time = self.current_log_time

                # Reset match state
                self.reset_current_match()
                self.write_waybar_json() # Force update to Lobby state

    def _identify_commander(self, grp_id, owner_seat):
        """Internal helper to identify and set commander info - STICKY VERSION"""
        info = get_card_info(grp_id)
        c_name = info.get("name", f"Card#{grp_id}")
        c_identity = info.get("color_identity", [])
        
        changed = False
        
        # Logic: Use known hero commander to anchor seatId
        is_hero = False
        if not self.is_generic_name(self.current_match["heroCommander"]) and self.current_match["heroCommander"] == c_name:
            if self.current_match["seatId"] != owner_seat and owner_seat != 0:
                self.log(f"[INFO] Correcting Hero Seat to {owner_seat} based on Commander {c_name}")
                self.current_match["seatId"] = owner_seat
                changed = True
            is_hero = True
        elif self.current_match["seatId"] is not None:
            is_hero = (owner_seat == self.current_match["seatId"])
        
        if is_hero:
            # STICKY LOGIC: Only set if current is generic/unknown
            if self.is_generic_name(self.current_match["heroCommander"]):
                self.current_match["heroCommanderId"] = grp_id
                self.current_match["heroCommander"] = c_name
                
                # Update colors from identity or name fallback
                identity = c_identity
                if not identity:
                    # Fallback: look up by name in cache
                    name_info = get_card_info_by_name(c_name)
                    identity = name_info.get("color_identity", [])
                
                if identity:
                    for c in identity:
                        if c not in self.current_match["deckColors"]:
                            self.current_match["deckColors"].append(c)
                    self.current_match["deckColors"].sort()
                    self.last_deck_colors = self.current_match["deckColors"]
                
                if self.is_generic_name(self.current_match.get("deckName")):
                    self.update_deck_name(f"Brawl: {c_name}")
                
                self.log(f"[INFO] Player's Commander Identified: {c_name} [{''.join(self.current_match['deckColors'])}]")
                self.write_waybar_json()
                changed = True
        else:
            # It's the opponent - STICKY LOGIC
            if self.is_generic_name(self.current_match["opponentCommander"]):
                # Removed the restriction: if c_name != self.current_match["heroCommander"]:
                # We want to identify the opponent's commander even in mirror matches.
                self.current_match["opponentCommanderId"] = grp_id
                self.current_match["opponentCommander"] = c_name
                
                # Update colors from identity or name fallback
                identity = c_identity
                if not identity:
                    name_info = get_card_info_by_name(c_name)
                    identity = name_info.get("color_identity", [])
                    
                for c in identity:
                    if c not in self.current_match["opponentColors"]:
                        self.current_match["opponentColors"].append(c)
                self.current_match["opponentColors"].sort()
                self.log(f"[INFO] Opponent's Commander Identified: {c_name} [{''.join(self.current_match['opponentColors'])}]")
                self.write_waybar_json()
                changed = True
        
        # PERSIST: Save state immediately when commander is identified
        if changed:
            self.save_state()


def export_stats(match_history, output_file):
    """Export match history to CSV or JSON."""
    if output_file.endswith('.json'):
        with open(output_file, 'w') as f:
            json.dump(match_history, f, indent=2)
        print(f"[INFO] Exported {len(match_history)} matches to {output_file}")
    elif output_file.endswith('.csv'):
        import csv
        if not match_history:
            print("[INFO] No matches to export")
            return
        
        with open(output_file, 'w', newline='') as f:
            fieldnames = match_history[0].keys()
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for match in match_history:
                row = match.copy()
                if isinstance(row.get('deck_colors'), list):
                    row['deck_colors'] = ''.join(row['deck_colors'])
                if isinstance(row.get('opponent_colors'), list):
                    row['opponent_colors'] = ''.join(row['opponent_colors'])
                if isinstance(row.get('rank'), dict):
                    row['rank'] = f"{row['rank'].get('class', '')} T{row['rank'].get('tier', '')}"
                writer.writerow(row)
        print(f"[INFO] Exported {len(match_history)} matches to {output_file}")


def print_statistics(match_history):
    """Print detailed statistics."""
    if not match_history:
        print("No matches recorded yet.")
        return
    
    total = len(match_history)
    wins = sum(1 for m in match_history if m.get("result") == "win")
    losses = sum(1 for m in match_history if m.get("result") == "loss")
    
    print(f"\n{'='*60}")
    print(f"OVERALL STATISTICS ({total} matches)")
    print(f"{'='*60}")
    print(f"Record: {wins}W - {losses}L ({wins/(wins+losses)*100:.1f}% win rate)")
    
    # By deck
    deck_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "colors": []})
    for m in match_history:
        deck = m.get("deck_name", "Unknown")
        result = m.get("result")
        colors = m.get("deck_colors", [])
        if result == "win":
            deck_stats[deck]["wins"] += 1
        elif result == "loss":
            deck_stats[deck]["losses"] += 1
        if colors and not deck_stats[deck]["colors"]:
            deck_stats[deck]["colors"] = colors
    
    if len(deck_stats) > 1:
        print(f"\n{'='*60}")
        print("BY DECK:")
        print(f"{'='*60}")
        for deck, stats in sorted(deck_stats.items(), key=lambda x: -(x[1]["wins"] + x[1]["losses"])):
            w, l = stats["wins"], stats["losses"]
            c = "".join(stats["colors"])
            total_games = w + l
            if total_games > 0:
                wr = w / total_games * 100
                color_str = f"({c})" if c else ""
                print(f"{deck:30} {color_str:6} {w:3}W - {l:3}L ({wr:5.1f}%) [{total_games:3} games]")
    
    # By format
    format_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
    for m in match_history:
        fmt = m.get("format", "Unknown")
        result = m.get("result")
        if result == "win":
            format_stats[fmt]["wins"] += 1
        elif result == "loss":
            format_stats[fmt]["losses"] += 1
    
    if len(format_stats) > 0:
        print(f"\n{'='*60}")
        print("BY FORMAT:")
        print(f"{'='*60}")
        for fmt, stats in sorted(format_stats.items(), key=lambda x: -(x[1]["wins"] + x[1]["losses"])):
            w, l = stats["wins"], stats["losses"]
            total_games = w + l
            if total_games > 0:
                wr = w / total_games * 100
                print(f"{fmt:20} {w:3}W - {l:3}L ({wr:5.1f}%) [{total_games:3} games]")
    
    # Matchups by Color
    matchup_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
    for m in match_history:
        my_colors = "".join(m.get("deck_colors", [])) or "Unknown"
        opp_colors = "".join(m.get("opponent_colors", [])) or "Unknown"
        key = f"{my_colors:5} vs {opp_colors}"
        
        result = m.get("result")
        if result == "win": matchup_stats[key]["wins"] += 1
        elif result == "loss": matchup_stats[key]["losses"] += 1
            
    if len(matchup_stats) > 0:
        print(f"\n{'='*60}")
        print("MATCHUPS BY COLOR (Your vs Opponent):")
        print(f"{'='*60}")
        for key, stats in sorted(matchup_stats.items()):
            w, l = stats["wins"], stats["losses"]
            total_games = w + l
            if total_games > 0:
                wr = w / total_games * 100
                print(f"{key:25} {w:3}W - {l:3}L ({wr:5.1f}%) [{total_games:3} games]")
    
    # Average game length
    durations = [m.get("duration_seconds", 0) for m in match_history if m.get("duration_seconds", 0) > 0]
    if durations:
        avg_duration = sum(durations) / len(durations)
        mins, secs = divmod(int(avg_duration), 60)
        print(f"\nAverage match duration: {mins}m {secs}s")
    
    # Play vs Draw
    on_play = sum(1 for m in match_history if m.get("going_first") == True and m.get("result") == "win")
    on_draw = sum(1 for m in match_history if m.get("going_first") == False and m.get("result") == "win")
    play_total = sum(1 for m in match_history if m.get("going_first") == True)
    draw_total = sum(1 for m in match_history if m.get("going_first") == False)
    
    if play_total > 0 or draw_total > 0:
        print(f"\nOn the Play:  {on_play}W - {play_total - on_play}L ({on_play/play_total*100:.1f}%)" if play_total > 0 else "")
        print(f"On the Draw:  {on_draw}W - {draw_total - on_draw}L ({on_draw/draw_total*100:.1f}%)" if draw_total > 0 else "")
        
    # Mulligan Stats
    print(f"\n{'='*60}")
    print("MULLIGAN STATISTICS:")
    print(f"{'='*60}")
    
    mulligan_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
    for m in match_history:
        mulls = m.get("mulligans", 0)
        result = m.get("result")
        if result == "win": mulligan_stats[mulls]["wins"] += 1
        elif result == "loss": mulligan_stats[mulls]["losses"] += 1
    
    for mulls in sorted(mulligan_stats.keys()):
        w, l = mulligan_stats[mulls]["wins"], mulligan_stats[mulls]["losses"]
        total = w + l
        wr = (w / total * 100) if total > 0 else 0
        print(f"Mulligans: {mulls} | {w}W - {l}L ({wr:.1f}%)")


def main():
    log_path = find_log_file()
    if not log_path:
        print("❌ Could not find Player.log in standard locations.")
        sys.exit(1)
        
    print("\n" + "═"*60)
    print("  MTGA PRO TRACKER - ENHANCED LOG MONITOR")
    print("═"*60)
    
    tracker = MTGATracker()
    atexit.register(tracker.save_state)
    atexit.register(save_card_cache)
    signal.signal(signal.SIGUSR1, tracker.reset_stats)

    # INITIAL SCAN: Find identity and match state if not already known
    tracker.quiet_mode = True
    tracker.recording_stats = False  # DO NOT record old matches during scan
    print(f"📡 Locating Log: {log_path.name}")
    print("🔍 Initializing tracker...", end="", flush=True)
    
    # 1. Quick full-file search for latest deck name - DISABLED to avoid pulling old decks
    # try:
    #     with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
    #         log_text = f.read()
    #         # Find the last occurrence of a deck name in V2 format
    #         deck_matches = re.findall(r'\\"Name\\":\\"([^\\"]+)\\"', log_text)
    #         if deck_matches:
    #             tracker.last_deck_name = deck_matches[-1]
    #             tracker.current_match["deckName"] = deck_matches[-1]
    # except Exception:
    #     pass

    # 2. Recent activity scan
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            # Focused scan on the last 10,000 lines for identity and match context
            lines = f.readlines()[-10000:]
            for line in lines:
                tracker.process_line(line)
        print(" Done.", flush=True)
        
        if tracker.hero_identity.get("screenName"):
            print(f"👤 Identity:  {tracker.hero_identity['screenName']}")
        
        my_c = "".join(tracker.last_deck_colors)
        deck_info = tracker.last_deck_name
        if my_c:
            deck_info += f" [{my_c}]"
            
        if tracker.current_match.get("active"):
            print(f"🎴 Deck:      {deck_info}")
        
        if tracker.current_match.get("active"):
            # STALENESS CHECK: If the last match activity was too long ago, it's not active.
            # Compare current match start/last activity with current time or last log timestamp.
            last_activity = tracker.current_log_time
            if time.time() - last_activity > 600: # 10 minutes
                tracker.log("[INFO] Stale match detected from log history. Setting status to LOBBY.")
                tracker.current_match["active"] = False
            else:
                print(f"⚔️  Status:    MID-GAME (Turn {tracker.current_match['maxTurns']})")
        
        if not tracker.current_match.get("active"):
            print("⚔️  Status:    LOBBY / WAITING")
            
    except Exception as e:
        print(f"\n[WARNING] Initial scan failed: {e}", flush=True)
    
    tracker.quiet_mode = False

    if "--stats" in sys.argv:
        print_statistics(tracker.match_history)
        sys.exit(0)

    if "--export" in sys.argv:
        try:
            idx = sys.argv.index("--export")
            output_file = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "mtga_matches.json"
        except (ValueError, IndexError):
            output_file = "mtga_matches.json"
        export_stats(tracker.match_history, output_file)
        sys.exit(0)

    if "--reset" in sys.argv:
        tracker.reset_stats()
        print("✅ Stats reset.")

    print("═"*60)
    print("🚀 Monitoring active. Waiting for match events...")
    print("═"*60 + "\n")
    
    tracker.scan_for_active_match(log_path)
    tracker.recording_stats = True  # START recording matches now
    try:
        for line in follow(log_path, initial_seek_end=True, check_interval=1):
            tracker.process_line(line)
    except KeyboardInterrupt:
        print("\n\n🛑 Tracker stopped.")


if __name__ == "__main__":
    main()
