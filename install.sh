#!/bin/bash

# Scry-Daemon - Install Script
# Magic the Gathering Arena Games and Deck Tracker (Linux Only)

PROJECT_NAME="scry-daemon"
echo "Installing $PROJECT_NAME..."
echo "-------------------------------"

# Create directories
mkdir -p ~/.cache/$PROJECT_NAME
mkdir -p ~/.local/share/$PROJECT_NAME
mkdir -p ~/.local/bin

# Copy files to ~/.local/bin/scry-daemon
INSTALL_DIR="$HOME/.local/bin/$PROJECT_NAME"
mkdir -p "$INSTALL_DIR"
cp *.py "$INSTALL_DIR/"
cp *.png "$INSTALL_DIR/"
cp waybar-status "$INSTALL_DIR/"
cp create-launcher.sh "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR"/*.py
chmod +x "$INSTALL_DIR/waybar-status"
chmod +x "$INSTALL_DIR/create-launcher.sh"

echo "Files installed to $INSTALL_DIR"

# Run the DB extractor to initialize the card cache
echo "Running initial database extraction..."
python3 "$INSTALL_DIR/db_extractor.py"

echo "-------------------------------"
echo "Setup complete!"
echo ""
echo "To create a desktop launcher for your application menu (e.g. Walker, GNOME), run:"
echo "bash $INSTALL_DIR/create-launcher.sh"
echo ""
echo "To start the tracker manually, run:"
echo "python3 $INSTALL_DIR/scry_daemon.py"
echo ""
echo "Note: The tracker should be running whenever you have MTG Arena open."
