#!/bin/bash

# Scry-Daemon Launcher Creator
# Creates a .desktop file for easy launching from application menus

PROJECT_NAME="scry-daemon"
DESKTOP_FILE="$HOME/.local/share/applications/scry-daemon.desktop"
INSTALL_DIR="$HOME/.local/bin/$PROJECT_NAME"
ICON_PATH="$INSTALL_DIR/LOGO.png"
EXEC_PATH="python3 $INSTALL_DIR/scry_daemon.py"

echo "Creating launcher for Scry-Daemon..."

cat <<EOF > "$DESKTOP_FILE"
[Desktop Entry]
Type=Application
Name=Scry-Daemon
GenericName=MTGA Match Tracker
Comment=Magic the Gathering Arena Games and Deck Tracker (Linux)
Exec=$EXEC_PATH
Icon=$ICON_PATH
Terminal=true
Categories=Game;Utility;
Keywords=mtga;magic;tracker;brawl;
EOF

chmod +x "$DESKTOP_FILE"

echo "-------------------------------"
echo "Launcher created at: $DESKTOP_FILE"
echo "You can now find 'Scry-Daemon' in your application launcher (e.g., Walker, Rofi, GNOME)."
echo "Note: The tracker will open in a terminal window so you can see its status."
echo "-------------------------------"
