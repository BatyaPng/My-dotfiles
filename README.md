# dotfiles

Configs for [GNU Stow](https://www.gnu.org/software/stow/). The repo mirrors
`$HOME`, so `stow .` symlinks every file into place.

| Branch    | Device                                     |
|-----------|--------------------------------------------|
| `main`    | Device-independent configs                 |
| `pc`      | Home PC (NVIDIA RTX 2080 Ti, Fedora KDE)   |
| `laptop`  | ThinkPad E16 Gen 2 (Fedora KDE)            |
| `work-pc` | Work machine                               |

Device branches are `main` plus a few commits with that device's
differences. Shared changes go to `main`, then each device branch is
rebased on it:

```sh
git switch pc && git rebase main
```

## Install

```sh
git clone <repo> ~/.dotfiles && cd ~/.dotfiles
git switch <device>
stow .
```

`.stowrc` sets `--no-folding`: every file is linked on its own, so programs
that create files next to their config (e.g. `systemctl --user enable`)
don't write into the repo.

Some programs (Plasma, Spectacle) save their config by replacing the file,
which turns the symlink into a regular file. Check with `stow -n -v .` and
copy changes back into the repo by hand.

## System setup (Fedora)

Things outside `$HOME` that stow can't handle.

CLI tools:

```sh
sudo dnf install -y neovim tmux stow wl-clipboard ripgrep fd-find fzf
```

Apps:

```sh
sudo rpm --import https://packages.microsoft.com/keys/microsoft.asc
printf '%s\n' '[code]' 'name=Visual Studio Code' \
    'baseurl=https://packages.microsoft.com/yumrepos/vscode' 'enabled=1' \
    'gpgcheck=1' 'gpgkey=https://packages.microsoft.com/keys/microsoft.asc' \
    | sudo tee /etc/yum.repos.d/vscode.repo
printf '%s\n' '[throne-repo]' 'name=Throne RPM repo' \
    'baseurl=https://parhelia512.github.io/rhel' 'enabled=1' 'gpgcheck=0' \
    | sudo tee /etc/yum.repos.d/throne.repo
sudo dnf install -y firefox alacritty code throne dolphin
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub md.obsidian.Obsidian org.telegram.desktop
```

NoMachine has no repo: download the x86_64 RPM from
<https://www.nomachine.com/download> and `sudo dnf install` it.

VS Code config and extensions (links settings/keybindings if needed, installs
missing extensions from `extensions.txt`; `--save` writes the installed ones
back to the list, `-n` is a dry run):

```sh
vscode-deploy
```

The `code` function in `.bashrc` runs `vscode-deploy --auto` first, which
deploys only when `extensions.txt` is newer than the last deploy or a config
link is broken, so after a `git pull` the next `code` brings VS Code up to date.

Kanata (input access without root, binary from GitHub releases):

```sh
sudo groupadd -f uinput
sudo usermod -aG input,uinput "$USER"
echo 'KERNEL=="uinput", MODE="0660", GROUP="uinput", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/99-uinput.rules
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
sudo udevadm control --reload-rules && sudo udevadm trigger
curl -fLo /tmp/kanata.zip https://github.com/jtroo/kanata/releases/latest/download/linux-binaries-x64.zip
unzip -o /tmp/kanata.zip kanata_linux_x64 -d /tmp && install -Dm755 /tmp/kanata_linux_x64 ~/.local/bin/kanata
# re-login for the new groups, then:
systemctl --user enable --now kanata
```

## Clipboard bridge (Wayland machines)

KWin syncs the X11 and Wayland clipboards only while an X11 window has focus,
so copies to and from `ssh -Y` sessions (and background X11 apps) get lost.
`clip-bridge` syncs them both ways, text and images; `clip-x11-watch` reports
X11 clipboard changes to it. The remote side (`xclip` wrapper on lps2) lives on
the `work-pc` branch.

```sh
sudo dnf install -y xclip
systemctl --user enable --now clip-bridge
```

## Bluetooth headphones on several adapters

`bt-duo` is a TUI for using several Bluetooth headphones at once, spread over
several adapters (built-in + USB dongles): connect, pair on a chosen adapter,
move between adapters, per-device volume. It lists audio devices only.

All connected Bluetooth outputs are also joined into one output, "Обе пары
наушников", which you pick in the KDE sound applet like any other. It runs in
its own PipeWire process (`bt-duo-combine.service`), so leaving a device out
of it (space in `bt-duo`) restarts only that process, not Bluetooth. Left-out
devices are kept in `~/.local/state/bt-duo/excluded`, not in git.

```sh
systemctl --user enable --now bt-duo-combine
```

## Obsidian

`Obsidian/.obsidian` is the config of the vault `~/Obsidian`. Unlike the
rest, the whole directory is one symlink, so settings files Obsidian creates
later (new plugins, snippets) land in the repo; its `.gitignore` drops
volatile state, plugin code and secrets. Create the link before `stow .`
(stow keeps it as is):

```sh
ln -s ../.dotfiles/Obsidian/.obsidian ~/Obsidian/.obsidian
```

Only remotely-save is tracked in full; other plugins keep just their
settings, so reinstall them from Community plugins. remotely-save credentials are not in
git: set them up again in its settings.
