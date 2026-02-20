import os
import sys
import json
from pathlib import Path

# Project Name
PROJECT_NAME = "scry-daemon"

# Base directories
HOME = Path.home()
CACHE_DIR = HOME / f".cache/{PROJECT_NAME}"
DATA_DIR = HOME / f".local/share/{PROJECT_NAME}"

# Create directories if they don't exist
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# State and Cache Files
STATE_FILE = DATA_DIR / "state.json"
CARD_CACHE_FILE = CACHE_DIR / "card_cache.json"
WAYBAR_JSON_FILE = CACHE_DIR / "waybar.json"
HTML_OUTPUT = CACHE_DIR / "stats.html"
DETAILS_DIR = CACHE_DIR / "match_details"
DECK_DETAILS_DIR = CACHE_DIR / "deck_details"

# Ensure directories for details exist
DETAILS_DIR.mkdir(parents=True, exist_ok=True)
DECK_DETAILS_DIR.mkdir(parents=True, exist_ok=True)

# Log file detection - Common Steam/Proton/Wine locations
POSSIBLE_LOG_PATHS = [
    Path("/mnt/Games/SteamLibrary/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"),
    HOME / ".local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log",
    HOME / ".steam/steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log",
    HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log",
    HOME / "Games/magic-the-gathering-arena/drive_c/users" / os.environ.get("USER", "user") / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log",
    HOME / ".wine/drive_c/users" / os.environ.get("USER", "user") / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log",
]

# Database detection
POSSIBLE_DB_GLOBS = [
    "/mnt/Games/SteamLibrary/steamapps/common/MTGA/MTGA_Data/Downloads/Raw/Raw_CardDatabase_*.mtga",
    str(HOME / ".local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw/Raw_CardDatabase_*.mtga"),
    str(HOME / ".steam/steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw/Raw_CardDatabase_*.mtga"),
    str(HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw/Raw_CardDatabase_*.mtga"),
]

# Asset paths (relative to the script directory)
BASE_PATH = Path(__file__).parent
LOGO_PATH = BASE_PATH / "LOGOWHITE.png"
FAVICON_PATH = BASE_PATH / "favicon.png"

def get_log_path():
    """Finds the log path or prompts the user."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                saved_path = state.get("log_path")
                if saved_path and Path(saved_path).exists():
                    return Path(saved_path)
        except: pass

    found_paths = [p for p in POSSIBLE_LOG_PATHS if p.exists()]
    
    if found_paths:
        if len(found_paths) == 1:
            return found_paths[0]
        
        print(f"\nMultiple {PROJECT_NAME} log files found:")
        for i, p in enumerate(found_paths):
            print(f"[{i+1}] {p}")
        
        choice = input(f"Select log file [1-{len(found_paths)}] (or enter custom path): ")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(found_paths):
                return found_paths[idx]
        except ValueError:
            if choice.strip():
                custom_path = Path(choice.strip()).expanduser()
                if custom_path.exists():
                    return custom_path

    print(f"\nMTGA Player.log not found in standard locations.")
    print("Hint: For Steam/Proton, it's usually in: ")
    print("~/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log")
    
    while True:
        path_str = input("\nPlease enter the full path to your Player.log: ").strip()
        if not path_str:
            print("Path is required to continue.")
            sys.exit(1)
        
        path = Path(path_str).expanduser()
        if path.exists() and path.is_file():
            try:
                state = {}
                if STATE_FILE.exists():
                    with open(STATE_FILE, 'r') as f: state = json.load(f)
                state["log_path"] = str(path)
                with open(STATE_FILE, 'w') as f: json.dump(state, f)
            except: pass
            return path
        else:
            print(f"Error: {path} does not exist or is not a file.")
