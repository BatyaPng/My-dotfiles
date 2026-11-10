#!/usr/bin/env bash
export LC_NUMERIC=C
input=$(cat)

# ANSI styles
RESET=$'\033[0m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
CYAN=$'\033[96m'
GREEN=$'\033[92m'
YELLOW=$'\033[93m'
RED=$'\033[91m'
GRAY=$'\033[90m'

BAR_WIDTH=10

color_for_pct() {
  local p=${1%.*}
  if   [ "$p" -lt 50 ]; then printf '%s' "$GREEN"
  elif [ "$p" -lt 80 ]; then printf '%s' "$YELLOW"
  else                       printf '%s' "$RED"
  fi
}

make_bar() {
  local pct=$1 color=$2 w=$BAR_WIDTH
  local filled empty i bar=""
  filled=$(awk -v p="$pct" -v w="$w" 'BEGIN { v=int(p*w/100+0.5); if(v<0)v=0; if(v>w)v=w; print v }')
  empty=$((w - filled))
  bar+="${color}"
  for ((i=0; i<filled; i++)); do bar+="█"; done
  bar+="${GRAY}"
  for ((i=0; i<empty;  i++)); do bar+="░"; done
  bar+="${RESET}"
  printf '%s' "$bar"
}

BOLD_GREEN=$'\033[01;32m'
BOLD_BLUE=$'\033[01;34m'

SEP="  ${GRAY}╱${RESET}  "

model=$(echo "$input" | jq -r '.model.display_name // "unknown"')
out="${BOLD}${CYAN}◆ ${model}${RESET}"

render_metric() {
  local label=$1 pct=$2 suffix=$3
  local c bar
  c=$(color_for_pct "$pct")
  bar=$(make_bar "$pct" "$c")
  printf '%s%s%s %s %s%3.0f%%%s%s' \
    "$DIM" "$label" "$RESET" "$bar" "$c" "$pct" "$RESET" "$suffix"
}


five_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
five_resets=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
if [ -n "$five_pct" ]; then
  suffix=""
  if [ -n "$five_resets" ]; then
    now=$(date +%s)
    delta=$((five_resets - now))
    if [ "$delta" -gt 0 ]; then
      h=$((delta / 3600))
      m=$(((delta % 3600) / 60))
      if [ "$h" -gt 0 ]; then
        left="${h}h${m}m"
      else
        left="${m}m"
      fi
      suffix=" ${DIM}↻ ${left}${RESET}"
    fi
  fi
  out+="${SEP}$(render_metric '5h' "$five_pct" "$suffix")"
fi

week_pct=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
week_resets=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')
if [ -n "$week_pct" ]; then
  suffix=""
  if [ -n "$week_resets" ]; then
    delta=$((week_resets - $(date +%s)))
    if [ "$delta" -gt 0 ]; then
      d=$((delta / 86400))
      h=$(((delta % 86400) / 3600))
      if [ "$d" -gt 0 ]; then
        left="${d}d${h}h"
      else
        left="${h}h$(((delta % 3600) / 60))m"
      fi
      suffix=" ${DIM}↻ ${left}${RESET}"
    fi
  fi
  out+="${SEP}$(render_metric '7d' "$week_pct" "$suffix")"
fi

cost=$(echo "$input" | jq -r '.cost.total_cost_usd // empty')
if [ -n "$cost" ]; then
  cost_fmt=$(awk -v c="$cost" 'BEGIN { printf "%.2f", c }')
  out+="${SEP}\$${cost_fmt}"
fi

# Session id, right-aligned on the same line
sid=$(echo "$input" | jq -r '.session_id // empty')
if [ -n "$sid" ]; then
  cols=${COLUMNS:-}
  if [ -z "$cols" ] && [ -n "$TMUX_PANE" ]; then
    cols=$(tmux display -p -t "$TMUX_PANE" '#{pane_width}' 2>/dev/null)
  fi
  [ -z "$cols" ] && cols=$(stty size </dev/tty 2>/dev/null | awk '{print $2}')
  RIGHT_MARGIN=4   # Claude Code pads the status line on both sides
  LEFT_MARGIN=0
  visible=$(printf '%s' "$out" | sed 's/\x1b\[[0-9;]*m//g' | LC_ALL=C.UTF-8 wc -m)
  pad=$(( ${cols:-0} - LEFT_MARGIN - visible - ${#sid} - RIGHT_MARGIN ))
  [ "$pad" -lt 2 ] && pad=2
  out+="$(printf '%*s' "$pad" '')${DIM}${sid}${RESET}"
fi

printf '%s' "$out"
