# Scry-Daemon
### Magic the Gathering Arena Games and Deck Tracker

**⚠️ NOTE: This tool is for Linux systems only.**

Scry-Daemon is a lightweight, high-performance log parser and match tracker for MTG Arena running on Linux via Steam (Proton) or Wine/Lutris.

## Features

- **Automated Match Tracking**: Records every game, including opponent names, deck colors, and win/loss results.
- **Brawl Focused**: Optimized for **Brawl and Historic Brawl**. While it works with all MTGA formats (Standard, Alchemy, etc.), color detection and match details are most accurate for Brawl matches.
- **Scry-Daemon Dashboard**: Generates a beautiful, locally-hosted HTML stats page for deep-diving into your match history.
- **Waybar Integration**: Real-time status updates directly in your Linux status bar.
- **Privacy Focused**: Operates entirely locally; your data never leaves your machine.

## Installation Guide

### 1. Prerequisites
Ensure you have Python 3.8 or higher installed on your system.

### 2. Locate your Player.log
While the script attempts to find this automatically, knowing its location is helpful. 
For Steam/Proton users, it is typically:
`~/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`

### 3. Clone and Install
```bash
# Clone the repository
git clone https://github.com/Balthazzahr/scry-daemon.git
cd scry-daemon

# Run the installer
bash install.sh
```

The installer will:
- Set up `~/.local/bin/scry-daemon`
- Initialize the card database by reading your local MTGA files.
- Create cache directories in `~/.cache/scry-daemon`

### 4. Initial Configuration
Run the tracker for the first time:
```bash
python3 ~/.local/bin/scry-daemon/scry_daemon.py
```
If your log file isn't found, the script will prompt you to enter the path manually. It will remember this path for future sessions.

## Usage

### Viewing Statistics
Your match history is rendered into a beautiful HTML file located at:
`~/.cache/scry-daemon/stats.html`

### Waybar Integration
Add this to your Waybar config:
```json
"custom/scry-daemon": {
    "format": "Scry: {}",
    "exec": "~/.local/bin/scry-daemon/waybar-status",
    "interval": 5,
    "return-type": "json"
}
```

## Configuration

The tracker attempts to automatically find your `Player.log` and MTGA database files in standard locations. If your installation is in a custom path, you can edit `config.py` in the installation directory.

## Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

## License

[MIT](https://choosealicense.com/licenses/mit/)
