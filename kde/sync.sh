#!/usr/bin/env bash
# Sync KDE taskbar launchers and Spectacle settings between machines.
#   save  - copy current settings of this machine into the repo
#   apply - apply settings from the repo to this machine
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHERS="$DOTFILES/kde/taskbar-launchers"
SPECTACLERC="$DOTFILES/.config/spectaclerc"
SHORTCUTS="$DOTFILES/.config/kglobalshortcutsrc"

save() {
    grep -m1 '^launchers=' ~/.config/plasma-org.kde.plasma.desktop-appletsrc \
        | cut -d= -f2- | tr ',' '\n' > "$LAUNCHERS"
    # lastImageSaveLocation changes on every screenshot, keep it out of git
    grep -v '^lastImageSaveLocation=' ~/.config/spectaclerc > "$SPECTACLERC"
    echo "Saved to $DOTFILES"
}

apply_launchers() {
    local list
    list="$(sed '/^$/d; s/.*/"&"/' "$LAUNCHERS" | paste -sd,)"
    gdbus call --session --dest org.kde.plasmashell --object-path /PlasmaShell \
        --method org.kde.PlasmaShell.evaluateScript "
        panels().forEach(function (p) {
            p.widgets().forEach(function (w) {
                if (w.type == 'org.kde.plasma.icontasks' || w.type == 'org.kde.plasma.taskmanager') {
                    w.currentConfigGroup = ['General'];
                    w.writeConfig('launchers', [$list]);
                    w.reloadConfig();
                }
            });
        });" > /dev/null
}

apply_spectacle() {
    local group key value
    while IFS= read -r line; do
        case "$line" in
            '['*']') group="${line:1:-1}" ;;
            *=*)
                key="${line%%=*}"
                value="${line#*=}"
                kwriteconfig6 --file spectaclerc --group "$group" --key "$key" -- "$value"
                ;;
        esac
    done < "$SPECTACLERC"

    # Spectacle global shortcuts, taken from the tracked kglobalshortcutsrc
    sed -n "/^\[services\]\[org.kde.spectacle.desktop\]/,/^\[/{/=/p}" "$SHORTCUTS" \
        | while IFS= read -r line; do
            value="${line#*=}"
            [ -n "$value" ] || continue
            # "\t" separates alternative shortcuts; kwriteconfig6 expects a real tab
            kwriteconfig6 --file kglobalshortcutsrc --group services --group org.kde.spectacle.desktop \
                --key "${line%%=*}" -- "${value//\\t/$'\t'}"
        done
}

case "${1:-}" in
    save) save ;;
    apply)
        apply_launchers
        apply_spectacle
        echo "Applied. Re-login for Spectacle shortcuts to take effect."
        ;;
    *) echo "Usage: $0 save|apply" >&2; exit 1 ;;
esac
