#!/usr/bin/env python3
import sqlite3
import json
import os
from pathlib import Path
import glob
import config

# Paths
def get_db_path():
    for db_glob in config.POSSIBLE_DB_GLOBS:
        db_files = glob.glob(db_glob)
        if db_files:
            return db_files[0]
    return None

CARD_CACHE_FILE = config.CARD_CACHE_FILE

def extract_mappings():
    # Find the database file
    db_path = get_db_path()
    if not db_path:
        print(f"Error: Could not find MTGA database in any expected location.")
        return

    print(f"Found MTGA Database: {db_path}")

    # Color Map for numeric IDs
    INT_COLOR_MAP = {1: 'W', 2: 'U', 3: 'B', 4: 'R', 5: 'G'}

    try:
        # Load existing cache if it exists
        cache = {}
        if CARD_CACHE_FILE.exists():
            with open(CARD_CACHE_FILE, 'r') as f:
                cache = json.load(f)
        
        # Connect to SQLite
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Query for GRPID to Name mapping
        query = """
        SELECT C.GrpId, L.Loc, C.ExpansionCode, C.Supertypes, C.Types, C.Colors, C.ColorIdentity
        FROM Cards C
        JOIN Localizations_enUS L ON C.TitleId = L.LocId
        GROUP BY C.GrpId;
        """
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        added_count = 0
        updated_count = 0
        
        def map_colors(csv_str):
            if not csv_str: return []
            try:
                # Some entries might be "1,2" or just "1"
                parts = str(csv_str).split(',')
                return sorted(list(set([INT_COLOR_MAP[int(c)] for c in parts if c.strip().isdigit() and int(c.strip()) in INT_COLOR_MAP])))
            except Exception:
                return []

        for grp_id, name, set_code, supertypes, types, colors, color_id in rows:
            str_id = str(grp_id)
            
            # Numeric Commander Check
            s_list = str(supertypes).split(',')
            t_list = str(types).split(',')
            is_legendary = '2' in s_list
            is_creature_or_pw = ('2' in t_list or '8' in t_list)
            is_commander = is_legendary and is_creature_or_pw

            # Type line reconstruction
            type_map = {'1':'Artifact', '2':'Creature', '3':'Enchantment', '4':'Instant', '5':'Land', '10':'Sorcery', '8':'Planeswalker', '11':'Battle', '13':'Vanguard', '14':'Emblem'}
            type_line = " ".join([type_map[t] for t in t_list if t in type_map])
            if is_legendary:
                type_line = "Legendary " + type_line
            
            # Basic info
            entry = {
                "id": grp_id,
                "name": name,
                "set": set_code,
                "is_legendary": is_legendary,
                "is_commander": is_commander,
                "type_line": type_line,
                "colors": map_colors(colors),
                "color_identity": map_colors(color_id)
            }
            
            if str_id in cache:
                old_entry = cache[str_id]
                # Preserve image URLs if we already have them
                entry["image_url"] = old_entry.get("image_url")
                entry["scryfall_uri"] = old_entry.get("scryfall_uri")
                
                # Update if name changed or if it was marked as not found
                if old_entry.get("name") != name or old_entry.get("not_found"):
                    cache[str_id] = entry
                    updated_count += 1
                # Also update if colors are missing
                elif not old_entry.get("colors") and entry["colors"]:
                    cache[str_id] = entry
                    updated_count += 1
            else:
                cache[str_id] = entry
                added_count += 1
        
        # Save updated cache
        CARD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CARD_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
            
        print(f"Successfully processed {len(rows)} cards.")
        print(f"Added {added_count} new cards, updated {updated_count} existing cards.")
        
        conn.close()
        
    except Exception as e:
        print(f"Error extracting data: {e}")

if __name__ == "__main__":
    extract_mappings()
