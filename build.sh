#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$HOME/Applications/DevTool.app"

# Kill running instance
pkill -f "DevTool.app/Contents/MacOS/DevTool" 2>/dev/null || true
sleep 1

# Ensure app structure
mkdir -p "$APP_PATH/Contents/MacOS"

# Compile directly into the app bundle
swiftc "$SCRIPT_DIR/dev.swift" \
  -o "$APP_PATH/Contents/MacOS/DevTool" \
  -framework Cocoa -framework IOKit

# Info.plist
cat > "$APP_PATH/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>DevTool</string>
    <key>CFBundleDisplayName</key>
    <string>DevTool</string>
    <key>CFBundleIdentifier</key>
    <string>com.shawyu.devtool.20260402</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>DevTool</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST

xattr -cr "$APP_PATH"

echo "✓ Built and installed DevTool.app"
echo "  Launch: Cmd+Space → DevTool"
