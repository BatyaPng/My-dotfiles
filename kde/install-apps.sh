#!/usr/bin/env bash
# Install the apps pinned in kde/taskbar-launchers (Fedora).
set -euo pipefail

# VS Code
if [ ! -f /etc/yum.repos.d/vscode.repo ]; then
    sudo rpm --import https://packages.microsoft.com/keys/microsoft.asc
    sudo tee /etc/yum.repos.d/vscode.repo > /dev/null <<'EOF'
[code]
name=Visual Studio Code
baseurl=https://packages.microsoft.com/yumrepos/vscode
enabled=1
autorefresh=1
type=rpm-md
gpgcheck=1
gpgkey=https://packages.microsoft.com/keys/microsoft.asc
EOF
fi

# Throne
if [ ! -f /etc/yum.repos.d/throne.repo ]; then
    sudo tee /etc/yum.repos.d/throne.repo > /dev/null <<'EOF'
[throne-repo]
name=Throne RPM repo
baseurl=https://parhelia512.github.io/rhel
enabled=1
skip_if_unavailable=True
gpgcheck=0
repo_gpgcheck=0
EOF
fi

# RPM Fusion (Telegram)
if ! rpm -q rpmfusion-free-release > /dev/null; then
    sudo dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm"
fi

sudo dnf install -y firefox alacritty code telegram-desktop throne dolphin

# Obsidian
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub md.obsidian.Obsidian

# NoMachine has no repo; install from a downloaded rpm
if ! rpm -q nomachine > /dev/null; then
    rpm_file="$(ls -t ~/Downloads/nomachine_*_x86_64.rpm 2>/dev/null | head -1 || true)"
    if [ -n "$rpm_file" ]; then
        sudo dnf install -y "$rpm_file"
    else
        echo "NoMachine: download the RPM (x86_64) from https://www.nomachine.com/download" \
             "into ~/Downloads and rerun this script." >&2
    fi
fi
