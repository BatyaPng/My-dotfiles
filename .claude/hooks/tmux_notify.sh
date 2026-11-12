#!/bin/bash
# Claude Code Stop/Notification hook: flag the tmux window and flash a message.
# Usage: tmux_notify.sh done|input   (hook JSON on stdin)
kind="$1"
input=$(cat)
[ -n "$TMUX_PANE" ] || exit 0
command -v tmux >/dev/null || exit 0

win=$(tmux display -p -t "$TMUX_PANE" '#{window_index}:#{window_name}' 2>/dev/null) || exit 0
tty=$(tmux display -p -t "$TMUX_PANE" '#{pane_tty}' 2>/dev/null)

if [ "$kind" = input ]; then
  detail=$(printf '%s' "$input" | jq -r '.message // "needs input"' 2>/dev/null)
  msg="Claude [$win]: ${detail:-needs input}"
else
  msg="Claude [$win]: done"
fi

# Bell on the pane's tty -> tmux marks the window in the status bar (window-status-bell-style)
[ -w "$tty" ] && printf '\a' > "$tty"

# Show a message on every attached client for 4s
for c in $(tmux list-clients -F '#{client_name}' 2>/dev/null); do
  tmux display-message -c "$c" -d 4000 "$msg" 2>/dev/null
done
exit 0
