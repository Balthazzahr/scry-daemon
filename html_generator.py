#!/usr/bin/env python3
import json
import os
import urllib.request
import urllib.parse
import time
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
import config

STATE_FILE = config.STATE_FILE
HTML_OUTPUT = config.HTML_OUTPUT
DETAILS_DIR = config.DETAILS_DIR
DECK_DETAILS_DIR = config.DECK_DETAILS_DIR
CARD_CACHE_FILE = config.CARD_CACHE_FILE
LOGO_PATH = config.LOGO_PATH
FAVICON_PATH = config.FAVICON_PATH

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

def get_logo_html(height="80px"):
    if LOGO_PATH.exists():
        try:
            import base64
            data = base64.b64encode(LOGO_PATH.read_bytes()).decode()
            return f'<img src="data:image/png;base64,{data}" style="height:{height}; max-width:100%;" alt="MTGA Logo">'
        except: pass
    return ""

def get_favicon_tag():
    if FAVICON_PATH.exists():
        try:
            import base64
            data = base64.b64encode(FAVICON_PATH.read_bytes()).decode()
            return f'<link rel="icon" type="image/png" href="data:image/png;base64,{data}">'
        except: pass
    return ""

CARD_CACHE = {}

def load_card_cache():
    global CARD_CACHE
    if CARD_CACHE_FILE.exists():
        try:
            with open(CARD_CACHE_FILE, 'r') as f:
                CARD_CACHE = {int(k): v for k, v in json.load(f).items()}
        except: pass

def get_card_scryfall_url(card_id, card_name=None):
    if card_id:
        try:
            cid = int(card_id)
            if cid in CARD_CACHE and CARD_CACHE[cid].get("scryfall_uri"):
                return CARD_CACHE[cid].get("scryfall_uri")
        except: pass
    if card_name: return f"https://scryfall.com/search?q={urllib.parse.quote(card_name)}"
    return "#"

def fetch_scryfall_image_by_name(name, card_id=None):
    if not name or "Unknown Card" in name: return None
    for card in CARD_CACHE.values():
        if card.get("name") == name and card.get("image_url"): return card.get("image_url")
    
    # 1. Try Scryfall Fuzzy
    try:
        url = f"https://api.scryfall.com/cards/named?fuzzy={urllib.parse.quote(name)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'MTGATrackerEnhanced/1.0'})
        with urllib.request.urlopen(req) as response:
            if response.getcode() == 200:
                data = json.loads(response.read().decode())
                img_url = data.get("image_uris", {}).get("large") or data.get("image_uris", {}).get("normal")
                if not img_url and "card_faces" in data:
                    img_url = data["card_faces"][0].get("image_uris", {}).get("large")
                
                if img_url:
                    if card_id and int(card_id) in CARD_CACHE:
                        CARD_CACHE[int(card_id)]["image_url"] = img_url
                        save_card_cache()
                    return img_url
    except: pass

    # 2. Try Gatherer Fallback (especially for OM1 / Reskins)
    try:
        # Search by name on Gatherer
        search_url = f"https://gatherer.wizards.com/Pages/Search/Default.aspx?name=+[%22{urllib.parse.quote(name)}%22]"
        req = urllib.request.Request(search_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            html = response.read().decode(errors='replace')
            # Look for card image patterns
            import re
            img_match = re.search(r'src="../../Handlers/Image\.ashx\?multiverseid=(\d+)&', html)
            if img_match:
                m_id = img_match.group(1)
                img_url = f"https://gatherer.wizards.com/Handlers/Image.ashx?multiverseid={m_id}&type=card"
                if card_id and int(card_id) in CARD_CACHE:
                    CARD_CACHE[int(card_id)]["image_url"] = img_url
                    save_card_cache()
                return img_url
    except: pass
    
    return None

def get_card_image(card_id, card_name=None):
    if card_id:
        try:
            cid = int(card_id)
            if cid in CARD_CACHE and CARD_CACHE[cid].get("image_url"):
                return CARD_CACHE[cid].get("image_url")
        except: pass
    
    if card_name and card_name != "Unknown":
        # Fallback: Dynamic Scryfall URL
        return f"https://api.scryfall.com/cards/named?format=image&exact={urllib.parse.quote(card_name)}"
    return None

def save_card_cache():
    try:
        with open(CARD_CACHE_FILE, 'w') as f: json.dump(CARD_CACHE, f)
    except: pass

def get_wr_color(wr):
    # Map 0-100 winrate to 0-120 HSL hue (Red to Green)
    # Using 65% lightness for readability on dark background
    hue = max(0, min(120, wr * 1.2))
    return f"hsl({hue}, 75%, 65%)"

COLOR_ICONS = {
    "W": "https://static.wikia.nocookie.net/mtgsalvation_gamepedia/images/8/8e/W.svg/revision/latest?cb=20160125094923",
    "U": "https://static.wikia.nocookie.net/mtgsalvation_gamepedia/images/9/9f/U.svg/revision/latest?cb=20160121092256",
    "B": "https://static.wikia.nocookie.net/mtgsalvation_gamepedia/images/2/2f/B.svg/revision/latest?cb=20160125093423",
    "R": "https://static.wikia.nocookie.net/mtgsalvation_gamepedia/images/8/87/R.svg/revision/latest?cb=20160125094913",
    "G": "https://static.wikia.nocookie.net/mtgsalvation_gamepedia/images/8/88/G.svg/revision/latest?cb=20160125094907",
    "C": "https://static.wikia.nocookie.net/mtgsalvation_gamepedia/images/1/1a/C.svg/revision/latest?cb=20160121092204"
}
COLOR_NAMES = { "W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless" }

def get_mana_cost_html(cost_str):
    if not cost_str: return ""
    import re
    # Handle MTGA database format: o2oW -> {2}{W}
    if cost_str.startswith('o'):
        # Split by 'o' and filter out empties
        symbols = [s for s in cost_str.split('o') if s]
    else:
        # Handle Scryfall format: {2}{W}
        symbols = re.findall(r'\{([^{}]+)\}', cost_str)
        
    html = ""
    for s in symbols:
        # Clean symbol for Scryfall SVG URL (e.g. {W/P} -> WP, {2/G} -> 2G)
        s_url = s.replace("/", "").replace("(", "").replace(")", "").upper()
        
        # Check if it's a known basic color for our local pips
        if s_url in COLOR_ICONS:
            html += f'<img src="{COLOR_ICONS[s_url]}" class="card-pip" style="width:16px; height:16px;" alt="{s}">'
        else:
            # Use Scryfall's symbol API for everything else
            html += f'<img src="https://svgs.scryfall.io/card-symbols/{s_url}.svg" class="card-pip" style="width:16px; height:16px;" alt="{s}">'
    return html

def get_type_symbols_html(info):
    """Generates symbols based on card type and color identity for cards without costs"""
    if not info: return ""
    
    html = ""
    type_line = str(info.get("type_line", ""))
    mana_cost = info.get("mana_cost", "")
    
    # 1. Add Type-specific symbols
    if "Planeswalker" in type_line:
        html += '<img src="https://svgs.scryfall.io/card-symbols/PW.svg" class="card-pip" style="width:16px; height:16px;" alt="PW">'
    
    # 2. For cards without mana costs (Lands, Tokens, etc), show color identity
    if not mana_cost or mana_cost == "":
        identity = info.get("color_identity", [])
        if not identity and "Land" in type_line:
            # Colorless land
            html += '<img src="https://svgs.scryfall.io/card-symbols/C.svg" class="card-pip" style="width:16px; height:16px;" alt="C">'
        else:
            for c in identity:
                if c in COLOR_ICONS:
                    html += f'<img src="{COLOR_ICONS[c]}" class="card-pip" style="width:16px; height:16px;" alt="{c}">'
                    
    return html

COMMON_CSS = """
    body { font-family: 'Segoe UI', sans-serif; background-color: #1e1e1e; color: #f0f0f0; margin: 0; padding: 20px; }
    .container { max-width: 1400px; min-width: 1200px; margin: auto; }
    .header-banner { text-align: center; margin-bottom: 30px; border-bottom: 2px solid #ff9800; padding-bottom: 20px; background: #222; border-radius: 15px 15px 0 0; padding-top: 25px; }
    .header-banner a { text-decoration: none; border: none; }
    h1, h2 { color: #ff9800; }
    .card { background: #333; padding: 12px; border-radius: 10px; flex: 1; min-width: 100px; text-align: center; border: 1px solid #444; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
    .card h3 { margin-top: 0; color: #ddd; font-size: 0.7em; text-transform: uppercase; margin-bottom: 8px; letter-spacing: 0.5px; }
    .card .value { font-size: 1.4em; font-weight: bold; }
    .win { color: #81c784; } .loss { color: #e57373; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 10px; background: #2a2a2a; border-radius: 12px; overflow: hidden; border: 1px solid #444; }
    th, td { padding: 14px 12px; text-align: left; border-bottom: 1px solid #444; }
    th { background: #1a1a1a; color: #ff9800; text-transform: uppercase; font-size: 0.75em; }
    tr:hover { background: #353535; }
    .badge { padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 0.85em; display: inline-block; min-width: 80px; text-align: center; }
    .badge-win { background: #2e7d32; color: #fff; } .badge-loss { background: #c62828; color: #fff; }
    .color-pip { width: 18px; height: 18px; border-radius: 50%; vertical-align: middle; margin-right: 4px; }
    .card-pip { width: 14px; height: 14px; border-radius: 50%; vertical-align: middle; margin-right: 0px; }
    .back-link { display: inline-block; margin-bottom: 20px; color: #64b5f6; text-decoration: none; font-weight: bold; }
    .pagination { display: flex; justify-content: center; gap: 10px; margin: 20px 0 40px; align-items: center; }
    .pagination button { background: #252525; color: #eee; border: 1px solid #555; padding: 8px 16px; border-radius: 6px; cursor: pointer; }
    .pagination button:disabled { opacity: 0.3; cursor: not-allowed; }
    .delete-btn { background: none; border: none; color: #e57373; font-size: 1.2em; cursor: pointer; padding: 5px 10px; border-radius: 4px; }
    .delete-btn:hover { background: rgba(229, 115, 115, 0.1); }
    .card-list { column-count: 2; list-style: none; padding: 0; margin-top: 10px; }
    .card-list li { margin-bottom: 8px; font-size: 0.9em; display: flex; align-items: center; }
    .mana-cost { display: inline-flex; gap: 1px; vertical-align: middle; width: 75px; justify-content: flex-end; margin-right: 12px; flex-shrink: 0; }
    .type-group { margin-bottom: 25px; }
    .type-header { font-size: 0.8em; text-transform: uppercase; color: #ff9800; border-bottom: 1px solid #444; padding-bottom: 5px; margin-bottom: 10px; font-weight: bold; letter-spacing: 1px; }
    th.sortable { cursor: pointer; position: relative; padding-right: 20px !important; }
    th.sortable:hover { background: #333 !important; color: #fff !important; }
    th.sortable::after { content: '↕'; position: absolute; right: 8px; opacity: 0.3; }
    @media (max-width: 800px) { .card-list { column-count: 1; } }

    .search-container { margin-bottom: 15px; text-align: right; }
    .search-input { background: #252525; color: #eee; border: 1px solid #555; padding: 10px 15px; border-radius: 8px; width: 300px; font-size: 0.9em; outline: none; }
    .search-input:focus { border-color: #ff9800; }

    #card-preview {
        position: fixed;
        display: none;
        z-index: 9999;
        pointer-events: none;
        border-radius: 12px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.7);
        max-width: 280px;
        border: 1px solid #555;
    }
"""

COMMON_JS = """
    let preview;
    document.addEventListener('DOMContentLoaded', () => {
        preview = document.createElement('img');
        preview.id = 'card-preview';
        document.body.appendChild(preview);
    });

    function showPreview(e, url) {
        if (!url || url === 'None' || url === '' || !preview) return;
        preview.src = url;
        preview.style.display = 'block';
        movePreview(e);
    }

    function hidePreview() {
        if (!preview) return;
        preview.style.display = 'none';
        preview.src = '';
    }

    function movePreview(e) {
        if (!preview) return;
        let x = e.clientX + 20;
        let y = e.clientY - 150;
        
        if (x + 300 > window.innerWidth) x = e.clientX - 300;
        if (y + 400 > window.innerHeight) y = window.innerHeight - 400;
        if (y < 10) y = 10;
        
        preview.style.left = x + 'px';
        preview.style.top = y + 'px';
    }

    function filterTable(tableId, inputId) {
        const input = document.getElementById(inputId);
        const filter = input.value.toLowerCase();
        const table = document.getElementById(tableId);
        const rows = Array.from(table.querySelectorAll('tbody tr'));
        rows.forEach(row => {
            const text = row.innerText.toLowerCase();
            row.style.display = text.includes(filter) ? '' : 'none';
        });
        if (filter === "") showPage(tableId, 1);
    }
"""

def generate_detail_page(m, index):
    res = m.get("result", "unknown")
    banner_class = "badge-win" if res == "win" else "badge-loss"
    hero_colors, opp_colors = m.get("deck_colors", []), m.get("opponent_colors", [])
    hero_pips = "".join([f'<img src="{COLOR_ICONS[c]}" class="color-pip" alt="{c}">' for c in hero_colors if c in COLOR_ICONS])
    opp_pips = "".join([f'<img src="{COLOR_ICONS[c]}" class="color-pip" alt="{c}">' for c in opp_colors if c in COLOR_ICONS])
    hero_img = get_card_image(m.get("hero_commander_id"), m.get("hero_commander"))
    opp_img = get_card_image(m.get("opponent_commander_id"), m.get("opponent_commander"))
    
    def get_card_list_html(card_ids, title):
        if not card_ids: return ""
        card_items = []
        for cid in sorted(list(set(card_ids))):
            info = CARD_CACHE.get(cid) or CARD_CACHE.get(str(cid), {})
            name = info.get("name", f"Card#{cid}")
            if "Unknown Card" not in str(name):
                img_url = info.get("image_url", "")
                if not img_url:
                    # Fallback to dynamic Scryfall image URL if missing from cache
                    img_url = f"https://api.scryfall.com/cards/named?format=image&exact={urllib.parse.quote(name)}"
                
                cost_html = get_mana_cost_html(info.get("mana_cost"))
                type_html = get_type_symbols_html(info)
                card_items.append(f'<li><div class="mana-cost">{type_html}{cost_html}</div><a href="{get_card_scryfall_url(cid, name)}" target="_blank" onmouseover="showPreview(event, \'{img_url}\')" onmouseout="hidePreview()" onmousemove="movePreview(event)" style="color:#bbb; text-decoration:none;">{name}</a></li>')
        if not card_items: return ""
        return f'<div class="section" style="margin-top:20px; background:#252525; padding:20px; border-radius:10px;"><div style="color:#ff9800; font-size:0.9em; text-transform:uppercase; border-bottom:1px solid #444; padding-bottom:5px; margin-bottom:10px; font-weight:bold;">{title}</div><ul class="card-list">{"".join(card_items)}</ul></div>'

    hero_cards_html = get_card_list_html(m.get("cards_seen", []), "Cards You Played / Seen")
    opp_cards_html = get_card_list_html(m.get("opponent_cards_seen", []), "Opponent Cards Played / Seen")

    duration = m.get("duration_seconds", 0)
    duration_str = f"{duration // 60}m {duration % 60}s" if duration else "Unknown"
    
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">{get_favicon_tag()}<title>Match Detail</title><style>{COMMON_CSS}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }}
        .section {{ background: #2a2a2a; padding: 20px; border-radius: 10px; border: 1px solid #444; }}
        .commander-img {{ width: 100%; max-width: 250px; border-radius: 12px; display: block; margin: 0 auto 10px; border: 1px solid #555; }}
        .match-stats {{ display: flex; gap: 15px; margin-top: 20px; }}
        .result-banner {{ width: 100%; padding: 15px 0; text-align: center; font-size: 1.8em; font-weight: bold; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 20px; border-radius: 8px; }}
    </style><script>{COMMON_JS}</script></head><body><div class="container">
        <div class="header-banner"><a href="../mtga_stats.html">{get_logo_html("140px")}</a></div>
        <a href="../mtga_stats.html" class="back-link">← Back to Statistics</a>
        
        <div class="result-banner {banner_class}">{res.upper()}</div>

        <div class="section" style="margin-bottom:20px;">
            <div style="color:#ff9800; font-size:0.9em; text-transform:uppercase; font-weight:bold; margin-bottom:15px;">Match Information</div>
            <div class="match-stats">
                <div class="card"><h3>Date</h3><div class="value" style="font-size:1.1em;">{m.get('date')}</div></div>
                <div class="card"><h3>Format</h3><div class="value" style="font-size:1.1em;">{m.get('format')}</div></div>
                <div class="card"><h3>Turns</h3><div class="value">{m.get('turns', 'N/A')}</div></div>
                <div class="card"><h3>Duration</h3><div class="value">{duration_str}</div></div>
                <div class="card"><h3>First/Draw</h3><div class="value">{'First' if m.get('going_first') else 'Draw' if m.get('going_first') is False else 'Unknown'}</div></div>
            </div>
        </div>

        <div class="grid">
            <div class="section">
                <div style="color:#bbb; font-size:0.8em; text-transform:uppercase;">Your Deck</div>
                <div style="font-size:1.4em;font-weight:bold;margin-bottom:15px;">{m.get('deck_name')} {hero_pips}</div>
                {f'<img src="{hero_img}" class="commander-img">' if hero_img else ''}
                <div style="text-align:center; color:#aaa; font-style:italic;">{m.get('hero_commander', '')}</div>
                {hero_cards_html}
            </div>
            <div class="section">
                <div style="color:#bbb; font-size:0.8em; text-transform:uppercase;">Opponent</div>
                <div style="font-size:1.4em;font-weight:bold;margin-bottom:15px;">{m.get('opponent')} {opp_pips}</div>
                {f'<img src="{opp_img}" class="commander-img">' if opp_img else ''}
                <div style="text-align:center; color:#aaa; font-style:italic;">{m.get('opponent_commander', '')}</div>
                {opp_cards_html}
            </div>
        </div>
    </div></body></html>"""
    with open(DETAILS_DIR / f"match_{index}.html", "w") as f: f.write(html)

def generate_deck_detail_page(deck_name, deck_stats, indexed_matches):
    commander_name, commander_id, all_cards_seen, deck_matches = None, None, set(), []
    for idx, m in indexed_matches:
        if m.get("deck_name") == deck_name:
            deck_matches.append((idx, m))
            if not commander_name and m.get("hero_commander"):
                commander_name, commander_id = m.get("hero_commander"), m.get("hero_commander_id")
            if m.get("cards_seen"):
                for cid in m.get("cards_seen"): all_cards_seen.add(cid)
    
    # Categorize cards
    categories = {
        "Creatures": [],
        "Planeswalkers": [],
        "Instants": [],
        "Sorceries": [],
        "Artifacts": [],
        "Enchantments": [],
        "Lands": [],
        "Spells": []
    }
    
    for cid in all_cards_seen:
        info = CARD_CACHE.get(cid) or CARD_CACHE.get(str(cid), {})
        name = info.get("name", f"Card#{cid}")
        if "Unknown Card" in str(name): continue
        
        type_line = str(info.get("type_line", ""))
        name = str(info.get("name", ""))
        img_url = info.get("image_url", "")
        if not img_url and name:
            # Fallback to dynamic Scryfall image URL if missing from cache
            img_url = f"https://api.scryfall.com/cards/named?format=image&exact={urllib.parse.quote(name)}"
            
        cost_html = get_mana_cost_html(info.get("mana_cost"))
        type_html = get_type_symbols_html(info)
        card_html = f'<li><div class="mana-cost">{type_html}{cost_html}</div><a href="{get_card_scryfall_url(cid, name)}" target="_blank" onmouseover="showPreview(event, \'{img_url}\')" onmouseout="hidePreview()" onmousemove="movePreview(event)" style="color:#bbb; text-decoration:none;">{name}</a></li>'
        
        # Land must be the FIRST check in case of artifacts-lands etc.
        # ADDED: name fallback for basics like Forest, Plains, etc.
        basics = ["Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"]
        if "Land" in type_line or name in basics or any(name.startswith(b + " ") for b in basics): 
            categories["Lands"].append(card_html)
        elif "Creature" in type_line: categories["Creatures"].append(card_html)
        elif "Planeswalker" in type_line: categories["Planeswalkers"].append(card_html)
        elif "Instant" in type_line: categories["Instants"].append(card_html)
        # ADDED: name fallback for Scapeshift or other missing sorcery types
        elif "Sorcery" in type_line or name == "Scapeshift": 
            categories["Sorceries"].append(card_html)
        elif "Artifact" in type_line: categories["Artifacts"].append(card_html)
        elif "Enchantment" in type_line: categories["Enchantments"].append(card_html)
        else: categories["Spells"].append(card_html)

    identified_html = ""
    for cat, items in categories.items():
        if items:
            identified_html += f'<div class="type-group"><div class="type-header">{cat} ({len(items)})</div><ul class="card-list">{"".join(sorted(items))}</ul></div>'

    w, l = deck_stats["wins"], deck_stats["losses"]
    wr = (w / (w+l) * 100) if (w+l) > 0 else 0
    
    # Play/Draw Stats
    pw, pl = deck_stats.get("play_wins", 0), deck_stats.get("play_losses", 0)
    dw, dl = deck_stats.get("draw_wins", 0), deck_stats.get("draw_losses", 0)
    p_wr = (pw / (pw+pl) * 100) if (pw+pl) > 0 else 0
    d_wr = (dw / (dw+dl) * 100) if (dw+dl) > 0 else 0

    # Daily winrate calculation
    daily_stats = defaultdict(lambda: {"w": 0, "l": 0})
    for _, match in deck_matches:
        d = match.get("date", "").split(" ")[0]
        if d:
            if match.get("result") == "win": daily_stats[d]["w"] += 1
            else: daily_stats[d]["l"] += 1
    
    sorted_days = sorted(daily_stats.keys())
    day_labels = sorted_days
    day_wrs = [(daily_stats[d]["w"] / (daily_stats[d]["w"] + daily_stats[d]["l"]) * 100) for d in sorted_days]

    hero_pips = "".join([f'<img src="{COLOR_ICONS[c]}" class="color-pip" alt="{c}">' for c in deck_stats.get("colors", []) if c in COLOR_ICONS])
    comm_img = get_card_image(commander_id, commander_name)
    history_rows = ""
    for idx, m in deck_matches[:50]:
        res = m.get("result", "unknown")
        opp_name = m.get("opponent") or "Unknown"
        opp_comm = m.get("opponent_commander") or "Unknown"
        opp_pips = "".join([f'<img src="{COLOR_ICONS[c]}" class="color-pip" alt="{c}">' for c in m.get("opponent_colors", []) if c in COLOR_ICONS])
        
        if opp_comm != "Unknown":
            display_opp = f"<b>{opp_comm}</b> {opp_pips}<br><span style='font-size:0.8em;color:#aaa;'>vs {opp_name}</span>"
        else:
            display_opp = f"<b>{opp_name}</b> {opp_pips}"
        
        history_rows += f"<tr onclick=\"window.location='../match_details/match_{idx}.html'\" style='cursor:pointer;'><td>{m.get('date')}</td><td><span class='badge {"badge-win" if res=="win" else "badge-loss"}'>{res.upper()}</span></td><td>{display_opp}</td><td>{m.get('format')}</td></tr>"
    
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{get_favicon_tag()}<title>{deck_name}</title><style>{COMMON_CSS}
        .layout {{ display: flex; gap: 40px; margin-top: 30px; }}
        .commander-img {{ width: 100%; border-radius: 15px; border: 1px solid #555; }}
        .charts-container {{ display: flex; justify-content: space-around; background: #252525; padding: 20px; border-radius: 15px; margin: 20px 0; border: 1px solid #444; }}
        .chart-box {{ width: 160px; height: 200px; text-align: center; }}
        .chart-box h3 {{ font-size: 0.7em; color: #aaa; margin-bottom: 10px; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>{COMMON_JS}</script>
    </head><body><div class="container">
        <div class="header-banner">{get_logo_html("140px")}</div>
        <a href="../mtga_stats.html" class="back-link">← Back to Statistics</a>
        <h1>{deck_name} {hero_pips}</h1>
        
        <div class="charts-container">
            <div class="chart-box"><h3>OVERALL WIN RATE</h3><canvas id="overallChart"></canvas></div>
            <div class="chart-box"><h3>PLAY WIN RATE</h3><canvas id="playChart"></canvas></div>
            <div class="chart-box"><h3>DRAW WIN RATE</h3><canvas id="drawChart"></canvas></div>
        </div>

        <script>
            function createPie(id, w, l) {{
                new Chart(document.getElementById(id), {{
                    type: 'doughnut',
                    data: {{
                        labels: ['Wins', 'Losses'],
                        datasets: [{{
                            data: [w, l],
                            backgroundColor: ['#2e7d32', '#c62828'],
                            borderWidth: 0,
                            cutout: '70%'
                        }}]
                    }},
                    options: {{
                        plugins: {{
                            legend: {{ display: false }},
                            tooltip: {{ enabled: true }},
                            centerText: {{ display: true, text: Math.round((w/(w+l||1))*100) + '%' }}
                        }},
                        maintainAspectRatio: false
                    }},
                    plugins: [{{
                        id: 'centerText',
                        beforeDraw: function(chart) {{
                            var width = chart.width, height = chart.height, ctx = chart.ctx;
                            ctx.restore();
                            var fontSize = (height / 114).toFixed(2);
                            ctx.font = fontSize + "em sans-serif";
                            ctx.textBaseline = "middle";
                            ctx.fillStyle = "#fff";
                            var text = chart.options.plugins.centerText.text,
                                textX = Math.round((width - ctx.measureText(text).width) / 2),
                                textY = height / 2;
                            ctx.fillText(text, textX, textY);
                            ctx.save();
                        }}
                    }}]
                }});
            }}

            createPie('overallChart', {w}, {l});
            createPie('playChart', {pw}, {pl});
            createPie('drawChart', {dw}, {dl});
        </script>

        <div class="layout">
            <div style="flex:0 0 300px; text-align:center;">
                <h2>Commander</h2>
                {f'<img src="{comm_img}" class="commander-img">' if comm_img else '<div style="background:#222; height:400px; border-radius:15px;">No Image</div>'}
                <div style="font-style:italic;color:#bbb;margin-top:10px;">{commander_name or "Unknown"}</div>
            </div>
            <div style="flex:1;"><h2>Identified Cards</h2>
                {identified_html}
            </div>
        </div>
        <h2>Recent History</h2><table><thead><tr><th>Date</th><th>Result</th><th>Opponent</th><th>Format</th></tr></thead><tbody>{history_rows}</tbody></table></div></body></html>"""
    safe_name = "".join([c for c in deck_name if c.isalnum() or c in (' ', '-', '_')]).strip().replace(' ', '_')
    with open(DECK_DETAILS_DIR / f"deck_{safe_name}.html", "w") as f: f.write(html)
    return f"deck_details/deck_{safe_name}.html"

    safe_name = "".join([c for c in deck_name if c.isalnum() or c in (' ', '-', '_')]).strip().replace(' ', '_')
    with open(DECK_DETAILS_DIR / f"deck_{safe_name}.html", "w") as f: f.write(html)
    return f"deck_details/deck_{safe_name}.html"

def generate_html():
    load_card_cache()
    if not STATE_FILE.exists(): return
    try:
        with open(STATE_FILE, 'r') as f: state = json.load(f)
    except: return
    matches = state.get("matches", [])
    if not matches: return
    DETAILS_DIR.mkdir(parents=True, exist_ok=True); DECK_DETAILS_DIR.mkdir(parents=True, exist_ok=True)
    def get_ts(m):
        ts = m.get("timestamp", 0)
        try: return datetime.fromisoformat(ts).timestamp() if isinstance(ts, str) and 'T' in ts else float(ts or 0)
        except: return 0
    matches.sort(key=get_ts, reverse=True)
    now = datetime.now()
    today_start = datetime.combine(now.date(), datetime.min.time()).timestamp()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0).timestamp()
    current_season_start = 0
    for code, date_str in SET_RELEASES:
        if date_str <= now.strftime("%Y-%m-%d"): current_season_start = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
        else: break
    def calc_stats(start_ts):
        w = sum(1 for m in matches if get_ts(m) >= start_ts and m.get("result") == "win")
        l = sum(1 for m in matches if get_ts(m) >= start_ts and m.get("result") == "loss")
        total = w + l
        wr = (w / total * 100) if total > 0 else 0
        return w, l, total, wr
    all_w, all_l, all_t, all_wr = calc_stats(0)
    day_w, day_l, day_t, day_wr = calc_stats(today_start)
    wk_w, wk_l, wk_t, wk_wr = calc_stats(week_start)
    sea_w, sea_l, sea_t, sea_wr = calc_stats(current_season_start)
    deck_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "colors": [], "play_wins": 0, "play_losses": 0, "draw_wins": 0, "draw_losses": 0})
    deck_charts_js = []
    for m in matches:
        deck = m.get("deck_name", "Unknown")
        res, colors, first = m.get("result"), m.get("deck_colors", []), m.get("going_first")
        if res == "win":
            deck_stats[deck]["wins"] += 1
            if first is True: deck_stats[deck]["play_wins"] += 1
            elif first is False: deck_stats[deck]["draw_wins"] += 1
        elif res == "loss":
            deck_stats[deck]["losses"] += 1
            if first is True: deck_stats[deck]["play_losses"] += 1
            elif first is False: deck_stats[deck]["draw_losses"] += 1
        
        # Color Identification Redundancy
        if not deck_stats[deck]["colors"]:
            if colors:
                deck_stats[deck]["colors"] = colors
            else:
                # Redundancy: Extract from commander or seen cards if deck_colors is missing
                found_colors = set()
                comm_id = m.get("hero_commander_id")
                comm_name = m.get("hero_commander")
                
                if comm_id:
                    info = CARD_CACHE.get(int(comm_id)) or CARD_CACHE.get(str(comm_id))
                    if info and info.get("color_identity"):
                        for c in info["color_identity"]: found_colors.add(c)
                
                if not found_colors and comm_name and comm_name != "Unknown":
                    # Try looking up by name in cache
                    for info in CARD_CACHE.values():
                        if info.get("name") == comm_name and info.get("color_identity"):
                            for c in info["color_identity"]: found_colors.add(c)
                            break
                
                if not found_colors:
                    for cid in m.get("cards_seen", []):
                        info = CARD_CACHE.get(int(cid)) or CARD_CACHE.get(str(cid))
                        if info and info.get("colors"):
                            for c in info["colors"]: found_colors.add(c)
                
                if found_colors:
                    deck_stats[deck]["colors"] = sorted(list(found_colors))
    
    # Daily winrate calculation for ALL matches
    daily_all_stats = defaultdict(lambda: {"w": 0, "l": 0})
    for m in matches:
        # Expected date format: "2026-02-17 12:34:56"
        d = m.get("date", "").split(" ")[0]
        if d:
            if m.get("result") == "win": daily_all_stats[d]["w"] += 1
            else: daily_all_stats[d]["l"] += 1
    
    sorted_all_days = sorted(daily_all_stats.keys())[-10:]
    all_day_labels = sorted_all_days
    all_day_wrs = [round(daily_all_stats[d]["w"] / (daily_all_stats[d]["w"] + daily_all_stats[d]["l"]) * 100) for d in sorted_all_days]

    html_content = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{get_favicon_tag()}<title>MTGA Tracker v2.0</title><style>{COMMON_CSS}
        .dashboard-header {{ display: flex; gap: 20px; margin-bottom: 30px; align-items: stretch; }}
        .pies-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; width: 450px; flex-shrink: 0; }}
        .stats-group {{ background:#252525; padding:10px; border-radius:12px; border:1px solid #555; text-align: center; }}
        .stats-group h2 {{ margin-top:0; font-size:0.7em; text-transform: uppercase; border-bottom:1px solid #444; padding-bottom:5px; margin-bottom:8px; color: #aaa; }}
        .mini-chart-box {{ width: 80px; height: 80px; margin: 0 auto; }}
        
        .history-chart-wrapper {{ flex: 1; background: #252525; border-radius: 12px; border: 1px solid #555; overflow: hidden; display: flex; flex-direction: column; }}
        .history-chart-title {{ font-size: 0.7em; text-transform: uppercase; padding: 10px; border-bottom: 1px solid #444; color: #aaa; text-align: center; font-weight: bold; }}
        .history-chart-scroll {{ flex: 1; overflow: hidden; padding: 10px; position: relative; }}
        .history-chart-container {{ height: 180px; min-width: 100%; }}

        /* Fixed Column Widths */
        #deckTable {{ table-layout: fixed; width: 100%; border-spacing: 0; }}
        #deckTable th {{ padding: 12px 2px; text-transform: uppercase; vertical-align: bottom; }}
        #deckTable td {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        
        .win-total {{ font-size: 1.3em; font-weight: bold; border-left: 1px solid #444; }}
        .win-sub {{ font-size: 0.9em; opacity: 0.8; }}

        #matchTable {{ table-layout: fixed; }}
        #matchTable th:nth-child(1), #matchTable td:nth-child(1) {{ width: 20%; }}
        #matchTable th:nth-child(2), #matchTable td:nth-child(2) {{ width: 10%; }}
        #matchTable th:nth-child(3), #matchTable td:nth-child(3) {{ width: 35%; }}
        #matchTable th:nth-child(4), #matchTable td:nth-child(4) {{ width: 25%; }}
        #matchTable th:nth-child(5), #matchTable td:nth-child(5) {{ width: 10%; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>{COMMON_JS}</script>
    <script>
        const pageSize = 10; const pageState = {{ 'deckTable': 1, 'matchTable': 1 }};
        const sortState = {{ 'tableId': null, 'col': null, 'dir': 1 }};

        function createMiniPie(id, w, l) {{
            if (!document.getElementById(id)) return;
            new Chart(document.getElementById(id), {{
                type: 'doughnut',
                data: {{
                    datasets: [{{
                        data: [w, l],
                        backgroundColor: ['#2e7d32', '#c62828'],
                        borderWidth: 0,
                        cutout: '75%'
                    }}]
                }},
                options: {{
                    plugins: {{
                        legend: {{ display: false }},
                        tooltip: {{ enabled: true }},
                        centerText: {{ display: true, text: (w+l > 0 ? Math.round((w/(w+l))*100) : 0) + '%' }}
                    }},
                    maintainAspectRatio: false
                }},
                plugins: [{{
                    id: 'centerText',
                    beforeDraw: function(chart) {{
                        var width = chart.width, height = chart.height, ctx = chart.ctx;
                        ctx.restore();
                        ctx.font = "bold 1.0em sans-serif";
                        ctx.textBaseline = "middle";
                        ctx.fillStyle = "#fff";
                        var text = chart.options.plugins.centerText.text,
                            textX = Math.round((width - ctx.measureText(text).width) / 2),
                            textY = height / 2;
                        ctx.fillText(text, textX, textY);
                        ctx.save();
                    }}
                }}]
            }});
        }}

        function showPage(tableId, page) {{
            const table = document.getElementById(tableId); const rows = Array.from(table.querySelectorAll('tbody tr'));
            const totalPages = Math.ceil(rows.length / pageSize) || 1;
            if (page < 1) page = 1; if (page > totalPages) page = totalPages;
            pageState[tableId] = page;
            rows.forEach((row, idx) => {{ row.style.display = (idx >= (page-1)*pageSize && idx < page*pageSize) ? '' : 'none'; }});
            document.getElementById(tableId + 'Prev').disabled = (page === 1);
            document.getElementById(tableId + 'Next').disabled = (page === totalPages);
            document.getElementById(tableId + 'Info').textContent = `Page ${{page}} of ${{totalPages}}`;
        }}

        function sortTable(tableId, colIdx, type='str') {{
            const table = document.getElementById(tableId);
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            if (sortState.col === colIdx) {{ sortState.dir *= -1; }}
            else {{ sortState.col = colIdx; sortState.dir = 1; }}

            rows.sort((a, b) => {{
                let valA = a.cells[colIdx].innerText.trim();
                let valB = b.cells[colIdx].innerText.trim();
                
                if (type === 'num') {{
                    valA = parseFloat(valA.replace(/[^0-9.-]/g, '')) || 0;
                    valB = parseFloat(valB.replace(/[^0-9.-]/g, '')) || 0;
                }} else if (type === 'wl') {{
                    valA = parseInt(valA.split('/')[0]) || 0;
                    valB = parseInt(valB.split('/')[0]) || 0;
                }} else if (type === 'color') {{
                    valA = Array.from(a.cells[colIdx].querySelectorAll('img')).filter(img => img.alt !== 'C').length;
                    valB = Array.from(b.cells[colIdx].querySelectorAll('img')).filter(img => img.alt !== 'C').length;
                    valA = -valA; valB = -valB;
                }}
                
                if (valA < valB) return -1 * sortState.dir;
                if (valA > valB) return 1 * sortState.dir;
                return 0;
            }});

            rows.forEach(row => tbody.appendChild(row));
            showPage(tableId, 1);
        }}

        async function deleteMatch(ts, btn) {{
            if (confirm('Delete?')) {{
                const r = await fetch(`http://localhost:8081/delete?ts=${{ts}}`);
                if (r.ok) window.location.reload();
            }}
        }}
        document.addEventListener('DOMContentLoaded', () => {{ 
            showPage('deckTable', 1); 
            showPage('matchTable', 1);
            createMiniPie('dayChart', {day_w}, {day_l});
            createMiniPie('wkChart', {wk_w}, {wk_l});
            createMiniPie('seaChart', {sea_w}, {sea_l});
            createMiniPie('allChart', {all_w}, {all_l});

            const historyCtx = document.getElementById('allHistoryChart').getContext('2d');
            const wrData = {json.dumps(all_day_wrs)};

            new Chart(historyCtx, {{
                type: 'bar',
                data: {{
                    labels: {json.dumps(all_day_labels)},
                    datasets: [{{
                        label: 'Win Rate %',
                        data: wrData,
                        backgroundColor: wrData.map(v => v === 50 ? '#fffdd0' : (v > 50 ? '#81c784' : '#e57373')),
                        borderRadius: 4,
                        barPercentage: 0.7
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        y: {{ 
                            beginAtZero: true, 
                            max: 100, 
                            grid: {{ color: 'rgba(255,255,255,0.05)', drawBorder: false }}, 
                            ticks: {{ color: '#aaa', callback: value => value + '%' }} 
                        }},
                        x: {{ grid: {{ display: false }}, ticks: {{ color: '#aaa' }} }}
                    }},
                    plugins: {{ 
                        legend: {{ display: false }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    return 'Win Rate: ' + context.parsed.y + '%';
                                }}
                            }}
                        }}
                    }}
                }},
                plugins: [{{
                    id: 'limitLine',
                    beforeDraw: (chart) => {{
                        const {{ctx, chartArea: {{top, right, bottom, left, width, height}}, scales: {{y}}}} = chart;
                        const y50 = y.getPixelForValue(50);
                        
                        ctx.save();
                        // Green background above 50%
                        ctx.fillStyle = 'rgba(46, 125, 50, 0.12)';
                        ctx.fillRect(left, top, width, y50 - top);
                        // Red background below 50%
                        ctx.fillStyle = 'rgba(198, 40, 40, 0.12)';
                        ctx.fillRect(left, y50, width, bottom - y50);
                        
                        // Persistent Dotted Line at 50%
                        ctx.beginPath();
                        ctx.setLineDash([5, 5]);
                        ctx.moveTo(left, y50);
                        ctx.lineTo(right, y50);
                        ctx.lineWidth = 2;
                        ctx.strokeStyle = 'rgba(255, 152, 0, 0.5)';
                        ctx.stroke();
                        ctx.restore();
                    }}
                }}, {{
                    id: 'barLabels',
                    afterDatasetsDraw: (chart) => {{
                        const {{ctx, data}} = chart;
                        ctx.save();
                        data.datasets[0].data.forEach((value, i) => {{
                            const meta = chart.getDatasetMeta(0);
                            const bar = meta.data[i];
                            const {{x, y}} = bar.getProps(['x', 'y'], true);
                            const base = chart.scales.y.getPixelForValue(0);
                            
                            let textColor;
                            if (value === 50) textColor = '#8d6e63'; // Darker beige
                            else if (value > 50) textColor = '#1b5e20'; // Darker green
                            else textColor = '#b71c1c'; // Darker red
                            
                            ctx.fillStyle = textColor;
                            ctx.font = 'bold 18px Segoe UI';
                            ctx.textAlign = 'center';
                            ctx.textBaseline = 'middle';
                            
                            const barHeight = base - y;
                            if (barHeight > 30) {{
                                ctx.fillText(value + '%', x, y + barHeight / 2);
                            }} else {{
                                ctx.fillText(value + '%', x, y - 12);
                            }}
                        }});
                        ctx.restore();
                    }}
                }}]
            }});
        }});
    </script></head><body><div class="container">
        <div class="header-banner"><a href="mtga_stats.html">{get_logo_html("160px")}</a></div>
        
        <div class="dashboard-header">
            <div class="pies-grid">
                <div class="stats-group"><h2>Today</h2><div class="mini-chart-box"><canvas id="dayChart"></canvas></div><div style="font-size:0.7em; color:#888; margin-top:5px;">{day_w}W - {day_l}L</div></div>
                <div class="stats-group"><h2>This Week</h2><div class="mini-chart-box"><canvas id="wkChart"></canvas></div><div style="font-size:0.7em; color:#888; margin-top:5px;">{wk_w}W - {wk_l}L</div></div>
                <div class="stats-group"><h2>This Season</h2><div class="mini-chart-box"><canvas id="seaChart"></canvas></div><div style="font-size:0.7em; color:#888; margin-top:5px;">{sea_w}W - {sea_l}L</div></div>
                <div class="stats-group"><h2>All Time</h2><div class="mini-chart-box"><canvas id="allChart"></canvas></div><div style="font-size:0.7em; color:#888; margin-top:5px;">{all_w}W - {all_l}L</div></div>
            </div>
            
            <div class="history-chart-wrapper">
                <div class="history-chart-title">Daily Win Rate %</div>
                <div class="history-chart-scroll">
                    <div class="history-chart-container" style="height: 260px;">
                        <canvas id="allHistoryChart"></canvas>
                    </div>
                </div>
            </div>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
            <h2>Deck Performance</h2>
            <div class="search-container"><input type="text" id="deckSearch" onkeyup="filterTable('deckTable', 'deckSearch')" class="search-input" placeholder="Search Decks..."></div>
        </div>
        <table id="deckTable">
            <colgroup>
                <col style="width: 28%;">
                <col style="width: 15%;">
                <col style="width: 8%;">
                <col style="width: 12%;">
                <col style="width: 10%;">
                <col style="width: 10%;">
                <col style="width: 17%;">
            </colgroup>
            <thead>
                <tr>
                    <th rowspan="2" class="sortable" style="text-align:center;" onclick="sortTable('deckTable', 0, 'str')">Deck Name</th>
                    <th rowspan="2" class="sortable" style="text-align:center;" onclick="sortTable('deckTable', 1, 'color')">Color Identity</th>
                    <th rowspan="2" class="sortable" style="text-align:center;" onclick="sortTable('deckTable', 2, 'num')">Games</th>
                    <th rowspan="2" class="sortable" style="text-align:center;" onclick="sortTable('deckTable', 3, 'wl')">Won / Lost</th>
                    <th colspan="3" style="text-align:center; border-bottom: 1px solid #555;">Win Rate</th>
                </tr>
                <tr>
                    <th class="sortable" style="font-size:0.7em; text-align:center; line-height: 1.2;" onclick="sortTable('deckTable', 4, 'num')">On The<br>Play</th>
                    <th class="sortable" style="font-size:0.7em; text-align:center; line-height: 1.2;" onclick="sortTable('deckTable', 5, 'num')">On The<br>Draw</th>
                    <th class="sortable" style="font-size:0.7em; text-align:center;" onclick="sortTable('deckTable', 6, 'num')">Total</th>
                </tr>
            </thead>
            <tbody>
"""
    indexed_matches = list(enumerate(matches))
    for i, (deck, stats) in enumerate(sorted(deck_stats.items(), key=lambda x: (x[1]["wins"] + x[1]["losses"]), reverse=True)):
        w, l = stats["wins"], stats["losses"]
        wr = (w / (w+l) * 100) if (w+l) > 0 else 0
        
        play_total = stats['play_wins'] + stats['play_losses']
        play_wr = (stats['play_wins'] / play_total * 100) if play_total > 0 else 0
        
        draw_total = stats['draw_wins'] + stats['draw_losses']
        draw_wr = (stats['draw_wins'] / draw_total * 100) if draw_total > 0 else 0
        
        pips = "".join([f'<img src="{COLOR_ICONS[c]}" class="color-pip" alt="{c}">' for c in stats["colors"] if c in COLOR_ICONS])
        
        html_content += f"""<tr>
            <td style='text-align:left; padding-left:15px;' title='{deck}'><a href='{generate_deck_detail_page(deck, stats, indexed_matches)}' style='color:#ff9800;text-decoration:none;font-weight:bold;'>{deck}</a></td>
            <td style='text-align:center;'>{pips}</td>
            <td style='text-align:center; font-size:1.2em;'>{w+l}</td>
            <td style='text-align:center; font-size:1.2em;'><span class='win'>{w}</span><span style='margin:0 8px; color:#666; font-size:0.8em;'>/</span><span class='loss'>{l}</span></td>
            <td style='color:{get_wr_color(play_wr)};text-align:center;' class='win-sub'>{play_wr:.1f}%</td>
            <td style='color:{get_wr_color(draw_wr)};text-align:center;' class='win-sub'>{draw_wr:.1f}%</td>
            <td style='color:{get_wr_color(wr)};text-align:center;' class='win-total'>{wr:.1f}%</td>
        </tr>"""
    html_content += """</tbody></table><div class="pagination"><button id="deckTablePrev" onclick="showPage('deckTable', pageState['deckTable']-1)">Prev</button><span id="deckTableInfo"></span><button id="deckTableNext" onclick="showPage('deckTable', pageState['deckTable']+1)">Next</button></div>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
            <h2>Recent Match History</h2>
            <div class="search-container"><input type="text" id="matchSearch" onkeyup="filterTable('matchTable', 'matchSearch')" class="search-input" placeholder="Search Matches..."></div>
        </div>
        <table id="matchTable"><thead><tr><th>Date</th><th>Result</th><th>Your Deck</th><th>Opponent</th><th style="text-align:right;">Actions</th></tr></thead><tbody>"""
    for i, m in enumerate(matches):
        res = m.get("result", "unknown")
        hero_pips = "".join([f'<img src="{COLOR_ICONS[c]}" class="color-pip" alt="{c}">' for c in m.get("deck_colors", []) if c in COLOR_ICONS])
        opp_pips = "".join([f'<img src="{COLOR_ICONS[c]}" class="color-pip" alt="{c}">' for c in m.get("opponent_colors", []) if c in COLOR_ICONS])
        
        opp_name = m.get("opponent") or "Unknown"
        opp_comm = m.get("opponent_commander") or "Unknown"
        
        if opp_comm != "Unknown":
            opp_display = f"<b>{opp_comm}</b> {opp_pips}<br><span style='font-size:0.8em;color:#aaa;'>vs {opp_name}</span>"
        else:
            opp_display = f"<b>{opp_name}</b> {opp_pips}"
            
        generate_detail_page(m, i)
        html_content += f"<tr onclick=\"window.location='match_details/match_{i}.html'\" style='cursor:pointer;'><td>{m.get('date')}</td><td><span class='badge {"badge-win" if res=="win" else "badge-loss"}'>{res.upper()}</span></td><td>{m.get('deck_name')} {hero_pips}</td><td>{opp_display}</td><td style='text-align:right;'><button class='delete-btn' onclick=\"event.stopPropagation(); deleteMatch('{m.get('timestamp')}', this)\">×</button></td></tr>"
    html_content += """</tbody></table><div class="pagination"><button id="matchTablePrev" onclick="showPage('matchTable', pageState['matchTable']-1)">Prev</button><span id="matchTableInfo"></span><button id="matchTableNext" onclick="showPage('matchTable', pageState['matchTable']+1)">Next</button></div>
        <p style="text-align:center;color:#888;font-size:0.8em;margin-top:40px;">Generated on """ + now.strftime("%Y-%m-%d %H:%M:%S") + """</p></div></body></html>"""
    with open(HTML_OUTPUT, 'w') as f: f.write(html_content)

if __name__ == "__main__": generate_html()
