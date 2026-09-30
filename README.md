# dotfiles

Branch `laptop`: ThinkPad E16 Gen 2, Fedora (KDE Plasma, Wayland).

## Install

```sh
git clone <repo> ~/.dotfiles && cd ~/.dotfiles
git switch laptop
./setup/fedora.sh          # CLI tools, uinput/udev, kanata binary (sudo)
./kde/install-apps.sh      # GUI apps from the taskbar (sudo)
stow .                     # symlink configs into $HOME
./kde/sync.sh apply        # taskbar launchers, Spectacle settings
# re-login for the input/uinput groups, then:
systemctl --user enable --now kanata
```

`.stowrc` sets `--no-folding`: without it `~/.config/systemd` becomes a symlink
into the repo and `systemctl --user enable` writes into it.

`.obsidian/` is not stowed, it belongs to the vault `~/Obsidian`:

```sh
mkdir -p ~/Obsidian && ln -s ~/.dotfiles/.obsidian ~/Obsidian/.obsidian
```

Only remotely-save is tracked in full. Other plugins keep just their settings,
so reinstall them from Community plugins (or download `main.js`/`styles.css`
of the version in `manifest.json`). remotely-save credentials are not in git:
set them up again in its settings.
