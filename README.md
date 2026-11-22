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

## Claude Code notifications in tmux

`.claude/hooks/tmux_notify.sh` rings the bell on the Claude pane, so tmux
marks its window in the status bar, and shows `Claude [win]: done` or the
"needs input" text on every attached client. It needs `jq` and does nothing
outside tmux. The `Stop` and `Notification` hooks that call it are in
`.claude/settings.json`.

## Docker workflow for Claude Code, Codex and Firefox

On Fedora, install the official Docker Engine packages and configure the
per-user daemon. The launcher connects to the rootless socket in
`$XDG_RUNTIME_DIR/docker.sock`. Setup builds both images from Ubuntu 24.04 LTS. The agents image
includes the native Linux Codex and Claude executables already installed on
this PC. The browser image installs Firefox from Mozilla’s signed APT
repository. Re-run setup after updating the CLIs or to rebuild older Fedora
images. The existing Docker volumes retain logins and browser data. Build
downloads use Docker’s normal network and require access to Docker Hub,
Ubuntu package mirrors, and packages.mozilla.org. [Fedora installation](https://docs.docker.com/engine/install/fedora/)
and [rootless prerequisites](https://docs.docker.com/engine/security/rootless/).

```sh
sudo dnf config-manager addrepo --from-repofile https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-ce-rootless-extras
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker
agent-proxied --setup-docker
agent-proxied --check
agent-proxied --tui
```

Clone and push from your host terminal using your existing Git credentials.
For an existing checkout, start at the `cd` step. Open a new interactive Bash
shell for the `codex` and `claude` aliases, or call `agent-proxied codex`
directly. Replace the example repository URL and feature branch name.

```sh
mkdir -p ~/Work
git clone git@github.com:OWNER/REPO.git ~/Work/my-project
cd ~/Work/my-project
git switch -c feature/my-change
codex login --device-auth   # first Codex login
codex                       # or: claude, then follow the login instructions
git status --short
git diff
git add -p                  # use git add -- path/to/file for new files
git diff --cached
git commit -m "Describe the change"
git push -u origin HEAD
```

The selected checkout is mounted read-write at `/workspace`. Launching from a
subdirectory preserves that working directory. Linked worktrees also receive
their shared Git metadata. Outside Git, the current directory becomes the
workspace. Starting an agent directly from `~` mounts your entire host home
read-write at `/workspace`, including hidden files and credentials. Each
agent gets its own persistent home volume at `/home/agent`. Container UID 0
maps to the invoking host user under rootless Docker, preserving host ownership
of edits. The containers drop capabilities, use a private process namespace,
and disable SELinux container labeling to use the existing workspace and user
sockets without relabeling them. Add any project-specific build tools to the
agents stage of the Dockerfile and rebuild.

Each agent or Firefox session gets all available physical cores except one
(including its sibling threads) and the machine's usable RAM minus 1 GiB.
The launcher keeps the only core on single-core machines. Every session
launched with the same available cores leaves the same core free. RAM caps
apply independently, and swap remains available subject to host policy.
Check the calculated limits with `agent-proxied --check`. In a container,
`nproc` shows the available threads and `cat /sys/fs/cgroup/memory.max`
shows its RAM cap in bytes. Tools reading `/proc/meminfo` can show host RAM.

Rebuild both images with `agent-proxied --setup-docker` after upgrading to
the resource-aware launcher. Rootless Docker needs cgroup v2, the systemd
driver, and delegated `cpuset` and `memory` controllers. If either controller
is missing, check the delegation for `user@.service` and restart the user
Docker daemon after applying the systemd change. [Docker rootless resource limits](https://docs.docker.com/engine/security/rootless/tips/#limiting-resources)

In the picker use arrows to highlight a profile, **Enter** to set the
default for new agents, **a** to add a VLESS link, **d** to delete a saved profile, **e** to edit the timezone
and hostname, **r** to refresh, and **q** to quit. Choose an IANA timezone
once (for example, `Europe/Berlin`). Each profile gets a stable, editable
hostname and uses `en_US.UTF-8` in the container and `en-US` in Firefox.
An interactive first launch prompts for a missing timezone. Set it with
**e** before a scripted launch.

Each session starts a private host Xray process from its selected profile.
Switching the default or editing a profile only affects future Docker
sessions. The egress check measures the shared default proxy on port 2083.
Use `PROXY_PORT=2080 agent-proxied --legacy claude` for a custom listener.

**f** launches Firefox on Wayland through the highlighted exit, with its own
Xray process and Docker volume. When opening an existing proxied Firefox
profile for the first time, close its windows and choose whether to copy its
cookies, saved logins, and history. Its cache is rebuilt inside the volume.
A failed copy can be retried. Each profile runs one browser session at a time.

Containers use loopback-only networking. Their local proxy listener reaches
the host proxy through one private Unix socket. Xray credentials stay in
`~/.config/claude-pinned/` on the host. Only Firefox receives the Wayland
display socket. To use the existing host CLIs with LAN and SSH access, run
`agent-proxied --legacy claude` or `agent-proxied --legacy codex`.
`claude-proxied` remains available for existing Bubblewrap callers.

On first use the picker saves the installed first outbound as **Original**.
Import a VLESS link with raw/TCP, WebSocket, gRPC or XHTTP transport.
For an existing config the picker replaces only its first outbound and
preserves its tag. For a new installation, activating the first profile
creates an HTTP listener on 127.0.0.1:2083. The config and saved profiles
contain credentials and belong in the private `~/.config/claude-pinned/`
directory outside the repository.

Install the [Xray-core release binary](https://github.com/XTLS/Xray-core/releases)
at `~/.local/bin/xray` after verifying its checksum against the release digest.
Install the dotfiles user unit and run:

```sh
systemctl --user daemon-reload
systemctl --user enable claude-xray-proxy.service
```

The picker starts the service when activating a profile. Use
`agent-proxied --check` for a full report and a bounded egress probe.
Normal launches run only local preflight checks. Set `PROXY_PORT` to use
another local proxy for a legacy launch or a diagnostic report.

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
