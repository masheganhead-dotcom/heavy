#!/bin/sh
# Installs heavy + memwatch for the current user. Re-run to update.
#   ./install.sh                 heavy + memwatch (always-on watcher)
#   ./install.sh --claude-shim   also queue scripted `claude -p` calls
#   ./install.sh --uninstall
set -e
SRC=$(cd "$(dirname "$0")" && pwd); BIN="$HOME/.local/bin"; PL="$HOME/Library/LaunchAgents/dev.heavy.memwatch.plist"
PY=$(command -v python3)
if [ "$1" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/dev.heavy.memwatch" 2>/dev/null || true
  rm -f "$PL" "$BIN/heavy" "$BIN/memwatch"
  [ -f "$BIN/claude" ] && grep -q heavy-shim "$BIN/claude" && rm -f "$BIN/claude"
  echo "heavy removed (state kept in ~/.local/state/heavy)"; exit 0
fi
mkdir -p "$BIN"; chmod +x "$SRC/heavy.py" "$SRC/memwatch.py" "$SRC/shims/claude"
ln -sf "$SRC/heavy.py" "$BIN/heavy"; ln -sf "$SRC/memwatch.py" "$BIN/memwatch"
cat > "$PL" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>dev.heavy.memwatch</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$SRC/memwatch.py</string><string>--loop</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>$BIN:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
<key>KeepAlive</key><true/><key>RunAtLoad</key><true/>
</dict></plist>
P
launchctl bootout "gui/$(id -u)/dev.heavy.memwatch" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PL"
if [ "$1" = "--claude-shim" ]; then ln -sf "$SRC/shims/claude" "$BIN/claude"; echo "claude shim installed ($BIN must come first on PATH)"; fi
echo "heavy installed. Try: heavy status"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: add $BIN to your PATH";; esac
