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
chmod +x "$INSTALL_DIR"/*.py
chmod +x "$INSTALL_DIR/waybar-status"

echo "Files installed to $INSTALL_DIR"

# Run the DB extractor to initialize the card cache
echo "Running initial database extraction..."
python3 "$INSTALL_DIR/db_extractor.py"

echo "-------------------------------"
echo "Setup complete!"
echo ""
echo "To start the tracker, run:"
echo "python3 $INSTALL_DIR/scry_daemon.py"
echo ""
echo "To view your stats, check:"
echo "~/.cache/$PROJECT_NAME/stats.html"
echo ""
echo "If you use Waybar, you can add a custom module using the 'waybar-status' script."
