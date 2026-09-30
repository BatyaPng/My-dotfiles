#!/usr/bin/env bash
# One-time system setup for Fedora: CLI tools and kanata. Needs sudo.
# GUI apps are installed by kde/install-apps.sh.
set -euo pipefail
cd "$(dirname "$0")"

sudo dnf install -y neovim tmux stow wl-clipboard ripgrep fd-find fzf

# kanata: access to input devices and uinput without root
sudo groupadd -f uinput
sudo usermod -aG input,uinput "$USER"
sudo install -m 0644 99-uinput.rules /etc/udev/rules.d/99-uinput.rules
echo uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
sudo udevadm control --reload-rules && sudo udevadm trigger

# kanata binary (not packaged in Fedora)
if ! command -v kanata >/dev/null && [ ! -x ~/.local/bin/kanata ]; then
    mkdir -p ~/.local/bin
    tmp=$(mktemp -d)
    curl -fL -o "$tmp/k.zip" \
        https://github.com/jtroo/kanata/releases/latest/download/linux-binaries-x64.zip
    unzip -q "$tmp/k.zip" -d "$tmp"
    install -m 0755 "$tmp/kanata_linux_x64" ~/.local/bin/kanata
    rm -rf "$tmp"
fi

echo "Done. Log out and back in (new groups), then run:"
echo "  systemctl --user enable --now kanata"
