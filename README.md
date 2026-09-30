# dotfiles

Branch `thinkpad-e16`: ThinkPad E16 Gen 2, Fedora (KDE Plasma, Wayland).

## Install

```sh
git clone <repo> ~/.dotfiles && cd ~/.dotfiles
git switch thinkpad-e16
./setup/fedora.sh                  # packages, uinput/udev, kanata binary (sudo)
stow --no-folding -t ~ .           # symlink configs into $HOME
# re-login for the input/uinput groups, then:
systemctl --user enable --now kanata
```

`--no-folding` matters: without it `~/.config/systemd` becomes a symlink into
the repo and `systemctl --user enable` writes into it.
