import json
import sqlite3
import glob
import urllib.request
import urllib.parse
import time
from pathlib import Path
import config

CACHE_FILE = config.CARD_CACHE_FILE

def get_db_path():
    for db_glob in config.POSSIBLE_DB_GLOBS:
        db_files = glob.glob(db_glob)
        if db_files:
            return db_files[0]
    return None

def refresh_cache():
    if not CACHE_FILE.exists():
        print("Cache missing.")
        return

    with open(CACHE_FILE, 'r') as f:
        cache = json.load(f)

    db_path = get_db_path()
    conn = None
    if db_path:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print(f"Using local DB: {db_path}")

    updated_count = 0
    total = len(cache)
    
    for i, (grp_id, info) in enumerate(cache.items()):
        # Check if we already have the new fields and they are not empty
        if info.get("mana_cost") and info.get("type_line"):
            continue
        
        # If it is a land, mana_cost will be empty, so we check type_line
        if info.get("type_line") and "Land" in info.get("type_line"):
            continue

        print(f"[{i+1}/{total}] Refreshing {grp_id} ({info.get('name')})...")
        
        # 1. Try Local DB
        found_local = False
        if conn:
            query = "SELECT C.OldSchoolManaText, C.Types FROM Cards C WHERE C.GrpId = ?"
            cursor.execute(query, (grp_id,))
            row = cursor.fetchone()
            if row:
                mana_cost, types = row
                # Artifact=1, Creature=2, Enchantment=3, Instant=4, Land=5, Sorcery=10, Planeswalker=8, Battle=11, Vanguard=13, Emblem=14
                type_map = {'1':'Artifact', '2':'Creature', '3':'Enchantment', '4':'Instant', '5':'Land', '10':'Sorcery', '8':'Planeswalker', '11':'Battle', '13':'Vanguard', '14':'Emblem'}
                t_list = str(types).split(',')
                type_line = " ".join([type_map[t] for t in t_list if t in type_map])
                
                info["mana_cost"] = mana_cost
                info["type_line"] = type_line
                found_local = True
                updated_count += 1

        # 2. Try Scryfall if local failed or to get better type_line
        if not found_local or not info.get("mana_cost"):
            try:
                url = f"https://api.scryfall.com/cards/arena/{grp_id}"
                req = urllib.request.Request(url, headers={'User-Agent': 'MTGATrackerEnhanced/1.0'})
                with urllib.request.urlopen(req) as response:
                    if response.getcode() == 200:
                        data = json.loads(response.read().decode())
                        info["mana_cost"] = data.get("mana_cost")
                        info["type_line"] = data.get("type_line")
                        updated_count += 1
                time.sleep(0.1) # Rate limit
            except Exception as e:
                print(f"  Scryfall fail for {grp_id}: {e}")

    if updated_count > 0:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
        print(f"Updated {updated_count} cards in cache.")
    else:
        print("No updates needed.")

    if conn:
        conn.close()

if __name__ == "__main__":
    refresh_cache()
