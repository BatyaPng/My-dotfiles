"""Local diagnostics and private VLESS profiles for agent-proxied."""
import fcntl
import hashlib
import json
import ipaddress
import os
import platform
import re
import select
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "share/agent-proxied"))
import docker_runtime as containers

ROOT = Path.home() / ".config/claude-pinned"
CONFIG = ROOT / "config.json"
STORE = ROOT / "profiles.json"
XRAY = Path.home() / ".local/bin/xray"
UNIT = "claude-xray-proxy.service"
BRIDGE = Path(__file__).resolve().with_name("claude-lan-bridge")
PIN = 2083


class Problem(ValueError):
    """A message safe to display, including after a credential parsing error."""


def clean(value):
    return "".join(c if c.isprintable() else "?" for c in str(value))


def run(args, timeout=8):
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def service(*args):
    result = run(["systemctl", "--user", *args, UNIT])
    return result is not None and result.returncode == 0


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.4):
            return True
    except PermissionError:
        return None
    except OSError:
        return False


def validate(path):
    if not os.access(XRAY, os.X_OK):
        raise Problem("Install the Xray-core release binary at ~/.local/bin/xray and make it executable.")
    result = run([XRAY, "run", "-test", "-c", path])
    if result is None or result.returncode:
        raise Problem("Xray rejected the config or timed out. Check the outbound and transport settings.")


def parse_link(raw):
    try:
        u = urlsplit(raw.strip())
        if u.scheme != "vless" or not u.hostname or u.password is not None or u.path not in ("", "/"):
            raise ValueError()
        uid = str(uuid.UUID(u.username or ""))
        host, port = u.hostname, u.port or 443
        if u.port == 0 or any(c.isspace() for c in host):
            raise ValueError()
        pairs = parse_qsl(u.query, keep_blank_values=True, strict_parsing=True)
        q = dict(pairs)
        if len(q) != len(pairs):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise Problem("Invalid VLESS link. Check the UUID, host, port and query fields.") from None

    network = q.pop("type", "tcp")
    network = {"tcp": "raw", "splithttp": "xhttp"}.get(network, network)
    security = q.pop("security", "none")
    # The pinned HTTP proxy carries TCP. XUDP is a share-link UDP setting
    # and needs no extra field in this Xray VLESS outbound.
    if q.pop("packetEncoding", "xudp") != "xudp":
        raise Problem("Unsupported packetEncoding. This importer accepts xudp.")
    if network not in ("raw", "ws", "grpc", "xhttp") or security not in ("tls", "reality", "none"):
        raise Problem("Unsupported transport or security option.")
    if network == "ws" and security == "reality":
        raise Problem("WebSocket with REALITY is unsupported.")
    stream = {"network": network, "security": security}
    user = {"id": uid, "encryption": q.pop("encryption", "none")}
    if "flow" in q:
        user["flow"] = q.pop("flow")

    def take(mapping):
        return {dest: q.pop(src) for src, dest in mapping.items() if src in q}

    if security in ("tls", "reality"):
        opts = take({"sni": "serverName", "fp": "fingerprint"})
        if security == "reality":
            opts.update(take({"pbk": "publicKey", "sid": "shortId", "spx": "spiderX"}))
            if not opts.get("publicKey"):
                raise Problem("REALITY requires a public key (pbk).")
        elif "alpn" in q:
            opts["alpn"] = q.pop("alpn").split(",")
        stream[security + "Settings"] = opts
    if network in ("ws", "xhttp"):
        opts = take({"path": "path", "host": "host"})
        if network == "xhttp":
            opts.update(take({"mode": "mode"}))
        stream[network + "Settings"] = opts
    elif network == "grpc":
        opts = take({"serviceName": "serviceName", "authority": "authority"})
        mode = q.pop("mode", "gun")
        if mode not in ("gun", "multi"):
            raise Problem("Unsupported gRPC mode.")
        opts["multiMode"] = mode == "multi"
        stream["grpcSettings"] = opts
    elif q.pop("headerType", "none") != "none":
        raise Problem("Unsupported raw headerType.")
    if q:
        key = next(iter(q))
        label = key if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,32}", key) else "(invalid key)"
        raise Problem("Unsupported link option: " + label)
    node = {"address": host, "port": port, "users": [user]}
    return {"protocol": "vless", "settings": {"vnext": [node]}, "streamSettings": stream}, clean(unquote(u.fragment))


def read_json(path):
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        raise Problem(f"Cannot read valid JSON from {path.name}. Restore or repair the file.") from None


def config():
    data = read_json(CONFIG)
    nodes = data.get("outbounds") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], dict):
        raise Problem("config.json needs a first outbound for the pinned exit.")
    if not isinstance(nodes[0].get("streamSettings", {}), dict):
        raise Problem("Repair streamSettings in the first configured outbound.")
    return data


@contextmanager
def locked():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.stat().st_mode & 0o077:
        ROOT.chmod(0o700)
    with (ROOT / ".profiles.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def staged(data):
    with tempfile.NamedTemporaryFile(dir=ROOT, suffix=".json", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        path.write_bytes(data)
        yield path
    finally:
        path.unlink(missing_ok=True)


def encoded(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()


def save(path, obj):
    with staged(encoded(obj)) as temp:
        temp.replace(path)


def outbound(cfg):
    node = cfg["outbounds"][0].copy()
    node.pop("tag", None)
    return node


def profiles():
    # Called under the lock. Snapshot the original once, before any activation.
    if STORE.exists():
        items = read_json(STORE)
        if not isinstance(items, dict) or any(
            not isinstance(node, dict) or not isinstance(node.get("streamSettings", {}), dict)
            for node in items.values()
        ):
            raise Problem("Repair profiles.json before managing profiles.")
        return items
    if not CONFIG.exists():
        return {}
    items = {"Original": outbound(config())}
    save(STORE, items)
    return items


def candidate(node):
    if CONFIG.exists():
        cfg = config()
    else:
        cfg = {"inbounds": [{"listen": "127.0.0.1", "port": PIN, "protocol": "http"}],
               "outbounds": [{"tag": "proxy"}]}
    old = cfg["outbounds"][0]
    cfg["outbounds"][0] = dict(node)
    if "tag" in old:
        cfg["outbounds"][0]["tag"] = old["tag"]
    return cfg


def add(name, node):
    name = name.strip()
    if not name or len(name) > 80 or not name.isprintable():
        raise Problem("Use a printable profile name of 1–80 characters.")
    with locked():
        items = profiles()
        if name.casefold() in {n.casefold() for n in items}:
            raise Problem("That profile name already exists.")
        with staged(encoded(candidate(node))) as temp:
            validate(temp)
        items[name] = node
        save(STORE, items)
    return name


def ready(action):
    if not service(action):
        return False
    for _ in range(25):
        if listening(PIN) and service("is-active", "--quiet"):
            return True
        time.sleep(.1)
    return False


def stop_child(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def default_host(name):
    return "workstation-" + hashlib.sha256(name.encode()).hexdigest()[:8]


def identity(name, value):
    if value is None:
        return {"timezone": "", "hostname": default_host(name)}
    if isinstance(value, list) and len(value) == 2:
        value = {"timezone": value[0], "hostname": default_host(name)}
    if not isinstance(value, dict):
        raise Problem("Repair the profile identity in regions.json.")
    zone, host = value.get("timezone"), value.get("hostname")
    if not isinstance(zone, str) or not isinstance(host, str):
        raise Problem("Enter a timezone and hostname for this profile.")
    containers.validate_timezone(zone)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host):
        raise Problem("Hostname must be one label of 1–63 lowercase letters, digits or hyphens.")
    return {"timezone": zone, "hostname": host}


def regions():
    path = ROOT / "regions.json"
    settings = read_json(path) if path.exists() else {}
    if not isinstance(settings, dict):
        raise Problem("Repair regions.json before launching a session.")
    return {name: identity(name, value) for name, value in settings.items()}


def change_region(name, zone, host):
    value = identity(name, {"timezone": zone.strip(), "hostname": host.strip()})
    with locked():
        if name not in profiles():
            raise Problem("Profile no longer exists. Refresh the picker.")
        settings = regions()
        settings[name] = value
        save(ROOT / "regions.json", settings)


def configured_name(items, current):
    path = ROOT / "default-profile.json"
    preferred = read_json(path) if path.exists() else None
    if isinstance(preferred, str) and preferred in items and items[preferred] == current:
        return preferred
    return next((name for name, node in items.items() if node == current), None)


def delete_profile(name, expected):
    with locked():
        items = profiles()
        if name not in items or items[name] != expected:
            raise Problem("Profile changed. Refresh and select it again.")
        current = outbound(config()) if CONFIG.exists() else None
        if configured_name(items, current) == name:
            raise Problem("Activate another default profile before deleting this one.")
        settings = regions()
        if name in settings:
            del settings[name]
            save(ROOT / "regions.json", settings)
        del items[name]
        save(STORE, items)


@dataclass(frozen=True)
class SessionProfile:
    name: str
    node: dict
    timezone: str
    hostname: str


def snapshot(name=None):
    with locked():
        items = profiles()
        if name is None:
            name = configured_name(items, outbound(config()))
        if name not in items:
            raise Problem("Select a saved profile in agent-proxied --tui.")
        settings = regions().get(name, identity(name, None))
        return SessionProfile(name, items[name], settings["timezone"], settings["hostname"])


def edit_identity(win, name, settings):
    zone = prompt(win, f"{name} · Timezone (IANA name) · locale en-US", settings["timezone"])
    if zone is None:
        return False
    host = prompt(win, "Hostname (Ctrl-U clears)", settings["hostname"])
    if host is None:
        return False
    change_region(name, zone, host)
    return True


def session_profile(name=None):
    profile = snapshot(name)
    if profile.timezone:
        return profile
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Problem(f"Timezone required for {profile.name}. Use e in agent-proxied --tui.")
    import curses
    if not curses.wrapper(edit_identity, profile.name, identity(profile.name, None)):
        raise Problem("Launch canceled.")
    return snapshot(profile.name)


def loopback_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def socks_ready(path):
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(.25)
            sock.connect(str(path))
            sock.sendall(b"\x05\x01\x00")
            with sock.makefile("rb") as stream:
                return stream.read(2) == b"\x05\x00"
    except OSError:
        return False


@contextmanager
def session_proxy(profile, protocol):
    with tempfile.TemporaryDirectory(prefix="agent-exit-") as directory:
        path = Path(directory) / "proxy.sock"
        inbound = {"listen": str(path), "protocol": protocol, "settings": {}}
        if protocol == "socks":
            # Xray's SOCKS handler advertises TCP only; HTTP also supports Unix.
            inbound.update(listen="127.0.0.1", port=loopback_port())
        cfg = {"inbounds": [inbound], "outbounds": [profile.node]}
        quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
        with staged(encoded(cfg)) as temp:
            validate(temp)
            proc = subprocess.Popen([XRAY, "run", "-c", temp], **quiet)
            bridge = None
            try:
                if protocol == "socks":
                    bridge = subprocess.Popen(["socat", f"UNIX-LISTEN:{path},fork,mode=0600",
                                               f"TCP:127.0.0.1:{inbound['port']}"], **quiet)
                for _ in range(100):
                    if proc.poll() is not None:
                        raise Problem(f"Xray exited with status {proc.returncode} starting {protocol} for {profile.name}.")
                    if bridge is not None and bridge.poll() is not None:
                        raise Problem(f"The Firefox socket bridge exited with status {bridge.returncode}.")
                    if path.is_socket() and (bridge is None or socks_ready(path)):
                        temp.unlink()
                        yield proc, containers.bind(directory, "/run/relay", True)
                        return
                    time.sleep(.05)
                raise Problem(f"Timed out waiting for the {protocol} listener for {profile.name}.")
            finally:
                try:
                    stop_child(bridge)
                finally:
                    stop_child(proc)


def browser_paths(name):
    key = hashlib.sha256(name.encode()).hexdigest()
    return ROOT / "firefox" / key, "agent-proxied-firefox-" + key


def firefox_session(name, migrate):
    containers.require_engine()
    containers.require_controllers()
    containers.require_image(containers.BROWSER)
    display = containers.wayland()
    profile = session_profile(name)
    directory, volume = browser_paths(name)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / ".session.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Problem("Close Firefox for this profile before launching or importing.")
        if migrate:
            containers.import_profile(directory, volume)
        browser = None
        session = "firefox-session-" + uuid.uuid4().hex
        quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        options = containers.container_args(session, profile.hostname, volume, profile.timezone)
        with session_proxy(profile, "socks") as (proxy, relay):
            try:
                browser = subprocess.Popen([*options, *display, *relay, "--shm-size=256m",
                                            containers.BROWSER, "firefox"], **quiet)
                try:
                    browser.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    (directory / ".docker-initialized").touch(mode=0o600)
                    try:
                        print("OK", flush=True)
                    except BrokenPipeError:
                        pass  # The picker may have closed during a large import.
                else:
                    raise Problem("Firefox exited during startup. Check the Wayland socket, locale and Docker image.")
                while proxy.poll() is None:
                    try:
                        browser.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if browser is not None:
                    containers.remove(session)
                    stop_child(browser)


def open_firefox(name, migrate=False):
    worker = subprocess.Popen([sys.executable, "-I", __file__, "firefox", name, "import" if migrate else "fresh"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    with worker.stdout as pipe:
        if not select.select([pipe], [], [], 120)[0]:
            raise Problem("Firefox startup is taking longer than expected. Check your desktop.")
        result = pipe.readline().strip()
    if result != "OK":
        raise Problem(result or "Firefox could not launch.")
    return "Opened Firefox · " + name


def activate(name, expected):
    with locked():
        result = activate_locked(name, expected)
        save(ROOT / "default-profile.json", name)
        return result


def activate_locked(name, expected):
    items = profiles()
    previous = CONFIG.read_bytes() if CONFIG.exists() else None
    current = outbound(config()) if previous is not None else None
    if current != expected or name not in items:
        raise Problem("The configured profile changed. Refresh and select it again.")
    if current == items[name]:
        if listening(PIN) and service("is-active", "--quiet"):
            return "Already active."
        if ready("restart"):
            return "Proxy started."
        raise Problem("Could not start the configured proxy. Review the status and repairs.")
    with staged(encoded(candidate(items[name]))) as temp:
        validate(temp)
        temp.replace(CONFIG)
    try:
        if ready("restart"):
            return "Activated " + name
    except KeyboardInterrupt:
        pass  # Restore the previous configuration if a restart is interrupted.
    if previous is None:
        CONFIG.unlink()
        service("stop")
        raise Problem("Activation failed. Removed the unsuccessful first config.")
    with staged(previous) as temp:
        temp.replace(CONFIG)
    if ready("restart"):
        raise Problem("Activation failed. Previous config restored and service recovered.")
    raise Problem("Activation failed. Previous config restored. Service recovery failed.")


@dataclass
class Check:
    state: str
    label: str
    repair: str = ""
    probe: str = ""


def diagnostics(port, full, command=None):
    rows = []

    def report(ok, label, remedy, probe=""):
        rows.append(Check("OK" if ok else "FAIL", label, "" if ok else remedy, probe))

    report(platform.system() == "Linux", "Linux namespace support", "Use a Linux host.")
    manager = next((pm for pm in ("dnf", "apt-get") if shutil.which(pm)), None)
    deps = {"socat": "socat", "python3": "python3"}
    if not full:
        deps.update({"bwrap": "bubblewrap", "nft": "nftables",
                     "ip": "iproute" if manager == "dnf" else "iproute2", "setpriv": "util-linux"})
    if port == PIN:
        deps["systemctl"] = "systemd"
    missing = [pkg for binary, pkg in deps.items() if not shutil.which(binary)]
    install = f"sudo {manager} install " if manager else "Install packages: "
    report(not missing, "Required tools" + (" missing: " + ", ".join(missing) if missing else " installed"),
           install + " ".join(missing))
    if full:
        try:
            limits = containers.resource_limits()
            rows.append(Check("WARN" if limits.single_core else "OK", limits.summary()))
        except containers.DockerError as error:
            rows.append(Check("FAIL", "Per-session limits", str(error)))
        try:
            containers.require_engine()
            rows.append(Check("OK", "Rootless Docker Engine"))
            try:
                containers.require_controllers()
                rows.append(Check("OK", "Docker cpuset and memory controllers"))
            except containers.DockerError as error:
                rows.append(Check("FAIL", "Docker resource controllers", str(error)))
            for image in (containers.AGENTS, containers.BROWSER):
                try:
                    containers.require_image(image)
                    rows.append(Check("OK", image))
                except containers.DockerError as error:
                    rows.append(Check("FAIL", image, str(error)))
        except containers.DockerError as error:
            rows.append(Check("FAIL", "Rootless Docker Engine", str(error)))
    else:
        report(os.access(BRIDGE, os.X_OK), "LAN bridge helper", "Restore claude-lan-bridge from the dotfiles.")
    if command:
        report(shutil.which(command), f"Command {clean(command)}", "Install the requested command or correct PATH.")
    if port == PIN:
        xray = os.access(XRAY, os.X_OK)
        report(xray, "Xray binary", "Install the verified Xray-core binary at ~/.local/bin/xray and chmod +x it.")
        try:
            config()
            if full and xray:
                validate(CONFIG)
            rows.append(Check("OK", "Pinned configuration"))
        except Problem as error:
            rows.append(Check("FAIL", "Pinned configuration", str(error) if CONFIG.exists() else
                              "Add a VLESS link and activate it, or restore ~/.config/claude-pinned/config.json."))
        if full and shutil.which("systemctl"):
            result = run(["systemctl", "--user", "show", "-p", "LoadState", "--value", UNIT])
            if result is None or result.returncode:
                rows.append(Check("FAIL", "User service manager unavailable", "Check systemctl --user status in your login session."))
            elif result.stdout.strip() != b"loaded":
                rows.append(Check("FAIL", "Proxy unit missing or unloadable",
                                  "Install the dotfiles user unit and run systemctl --user daemon-reload."))
            else:
                report(service("is-active", "--quiet"), "Pinned proxy service",
                       "systemctl --user start " + UNIT, "service")
    reachable = listening(port)
    if reachable is None:
        rows.append(Check("FAIL", f"Listener 127.0.0.1:{port} probe denied",
                          "Socket access is restricted in this process. Listener status is unknown."))
    else:
        report(reachable, f"Listener 127.0.0.1:{port} " + ("online" if reachable else "unavailable"),
           "Check the pinned proxy service and its listener." if port == PIN else "Start the selected local proxy or correct PROXY_PORT.")
    if full:
        if not shutil.which("curl"):
            rows.append(Check("WARN", "Optional egress tool missing: curl", install + "curl"))
        if not reachable:
            rows.append(Check("SKIP", "Egress probe requires a reachable listener"))
        elif not shutil.which("curl"):
            rows.append(Check("SKIP", "Egress probe requires curl"))
        else:
            result = run(["curl", "-q", "--fail", "--silent", "--max-time", "5", "--noproxy", "",
                          "--proxy", f"http://127.0.0.1:{port}", "https://api.ipify.org"])
            try:
                ip = ipaddress.ip_address(result.stdout.strip().decode()) if result and not result.returncode else None
            except (ValueError, UnicodeError):
                ip = None
            label = "Default proxy egress" if port == PIN else "Egress"
            report(ip, f"{label} {ip}" if ip else f"{label} probe failed",
                   "Check the selected node and connectivity to api.ipify.org through the proxy.", "egress")
    return rows


def prompt(win, label, initial="", secret=False):
    import curses
    value = list(initial)
    while True:
        win.erase()
        height, width = win.getmaxyx()
        display = ("*" * min(len(value), 12)) if secret else "".join(value)
        for y, line in enumerate((label, display[-max(1, width-2):], "Enter accept   Esc cancel")):
            if y < height - 1:
                win.addnstr(y, 0, line, max(0, width-1))
        win.refresh()
        key = win.get_wch()
        if key in ("\r", "\n", curses.KEY_ENTER):
            return "".join(value)
        if key == "\x1b":
            return None
        if key == "\x15":
            value.clear()
        elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
            if value:
                value.pop()
        elif isinstance(key, str) and key.isprintable() and len(value) < 16384:
            value.append(key)


def terminal_styles():
    import curses
    colors = {"OK": curses.COLOR_GREEN, "WARN": curses.COLOR_YELLOW,
              "FAIL": curses.COLOR_RED, "heading": curses.COLOR_CYAN}
    styles = dict.fromkeys(colors, 0)
    styles["SKIP"] = curses.A_DIM
    if curses.has_colors() and not os.environ.get("NO_COLOR"):
        try:
            curses.use_default_colors()
            for pair, (role, color) in enumerate(colors.items(), 1):
                curses.init_pair(pair, color, -1)
                styles[role] = curses.color_pair(pair)
        except curses.error:
            pass
    return styles


def dashboard(win):
    import curses
    curses.noecho()
    win.keypad(True)
    styles = terminal_styles()
    heading = styles["heading"] | curses.A_BOLD
    selected = scroll = 0
    message = ""
    message_style = 0
    while True:
        win.erase()
        win.addnstr(0, 0, "Checking pinned proxy...", max(0, win.getmaxyx()[1]-1))
        win.refresh()
        rows = diagnostics(PIN, True)
        passed = {row.probe for row in rows if row.state == "OK"}
        working = {"service", "egress"} <= passed
        current = None
        items = {}
        default = None
        regional = {}
        try:
            with locked():
                regional = regions()
                items = profiles()
                current = outbound(config()) if CONFIG.exists() else None
                default = configured_name(items, current)
        except (Problem, containers.DockerError, OSError) as error:
            rows.append(Check("FAIL", "Profile storage", clean(error)))
        names = list(items)
        selected = min(selected, max(0, len(names)-1))
        while True:
            h, w = win.getmaxyx()
            width = max(1, w-1)
            page = max(1, h-4)
            win.erase()
            state = "BLOCKED" if any(r.state == "FAIL" for r in rows) else (
                "WARNING" if any(r.state == "WARN" for r in rows) else "READY")
            state_style = styles[{"BLOCKED": "FAIL", "WARNING": "WARN", "READY": "OK"}[state]]
            win.addnstr(0, 0, "agent-proxied   |   " + state, width, state_style | curses.A_BOLD)
            lines = [("Profiles · [x] default proxy with verified egress", heading)]
            for idx, name in enumerate(names):
                node = items[name]
                stream = node.get("streamSettings") or {}
                desc = " / ".join(str(v) for v in (node.get("protocol"), stream.get("network"), stream.get("security")) if v)
                active = name == default and working
                checkbox = "[x] " if active else "[ ] "
                mark = " [default for new agents]" if name == default else ""
                attr = styles["OK"] if active else styles["WARN"] if mark else 0
                if selected == idx:
                    attr |= curses.A_REVERSE
                lines.append((("> " if selected == idx else "  ") + checkbox + clean(name + mark + "   " + desc), attr))
            if names:
                name = names[selected]
                details = regional.get(name, identity(name, None))
                lines.append((f"  {details['hostname']} · {details['timezone'] or 'Timezone required'} · en-US", 0))
            if not names:
                lines.append(("  No saved profiles", 0))
            elif current is not None and current not in items.values():
                lines.append(("  Configured outbound was edited outside the picker", styles["WARN"]))
            lines.extend([("", 0), ("Status", heading)])
            for row in rows:
                lines.extend((line, styles[row.state]) for line in textwrap.wrap(f"{row.state:4}  {row.label}", width))
                if row.repair:
                    lines.extend((line, styles["WARN"]) for line in textwrap.wrap("      " + row.repair, width))
            scroll = min(scroll, max(0, len(lines)-page))
            for i, (line, attr) in enumerate(lines[scroll:scroll+page], 1):
                if i < h-2:
                    win.addnstr(i, 0, line, width, attr)
            if h >= 3:
                win.addnstr(h-2, 0, message, width, message_style)
                win.addnstr(h-1, 0, "↑↓ Select  a Add  d Delete  Enter Activate  f Firefox  e Region  r Recheck  q Quit", width)
            win.refresh()
            key = win.get_wch()
            try:
                if key in ("q", "\x1b"):
                    return
                if key in (curses.KEY_UP, curses.KEY_DOWN):
                    selected = max(0, min(len(names)-1, selected + (1 if key == curses.KEY_DOWN else -1)))
                    scroll = max(0, min(scroll, selected+1), selected+2-page)
                elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                    scroll = max(0, scroll + (page if key == curses.KEY_NPAGE else -page))
                elif key == "r":
                    message = ""
                    break
                elif key == "f" and names:
                    name = names[selected]
                    if not regional.get(name):
                        if not edit_identity(win, name, identity(name, None)):
                            continue
                        regional = regions()
                    directory, _ = browser_paths(name)
                    migrate = False
                    if (directory / "prefs.js").is_file() and not (directory / ".docker-initialized").exists():
                        answer = prompt(win, "Copy existing Firefox data? y = import, n = fresh, Esc = cancel")
                        if answer not in ("y", "n"):
                            continue
                        migrate = answer == "y"
                    message = open_firefox(name, migrate)
                    message_style = styles["OK"]
                elif key == "e" and names:
                    name = names[selected]
                    if edit_identity(win, name, regional.get(name, identity(name, None))):
                        message = "Identity saved for future sessions."
                        message_style = styles["OK"]
                        break
                elif key == "d" and names:
                    name = names[selected]
                    if name == default:
                        raise Problem("Activate another default profile before deleting this one.")
                    if prompt(win, f"Delete VPN profile {name}? Type y to delete.") == "y":
                        delete_profile(name, items[name])
                        message = "Deleted VPN profile " + name
                        message_style = styles["OK"]
                        break
                elif key == "a":
                    raw = prompt(win, "Paste a VLESS link (masked)", secret=True)
                    if raw is not None:
                        node, suggested = parse_link(raw)
                        name = prompt(win, "Profile name", suggested)
                        if name is not None:
                            name = add(name, node)
                            with locked():
                                selected = list(profiles()).index(name)
                            message = "Saved. Press Enter to activate."
                            message_style = styles["OK"]
                            scroll = max(0, selected+2-page)
                            break
                elif key in ("\n", "\r", curses.KEY_ENTER) and names:
                    name = names[selected]
                    if items[name] != current:
                        old = default or ("current config" if current else "none")
                        answer = prompt(win, f"{old} → {name}. Set default for new agents. Type y to apply.")
                        if answer != "y":
                            continue
                    message = activate(name, current)
                    message_style = styles["OK"]
                    break
            except (OSError, Problem, containers.DockerError) as error:
                message = clean(error)
                message_style = styles["FAIL"] | curses.A_BOLD
                break


def exit_session(signum, _frame):
    # Unwind Docker and Xray contexts when the terminal or service closes.
    raise SystemExit(128 + signum)


def main():
    os.umask(0o077)
    mode = sys.argv[1]
    if mode in ("docker-agent", "firefox"):
        signal.signal(signal.SIGHUP, exit_session)
        signal.signal(signal.SIGTERM, exit_session)
    if mode == "setup-docker":
        containers.setup()
        print("Docker images are ready.")
        return 0
    if mode == "docker-agent":
        command = sys.argv[2]
        if "PROXY_PORT" in os.environ:
            raise Problem(f"Custom PROXY_PORT requires agent-proxied --legacy {command}.")
        try:
            containers.require_engine()
            containers.require_controllers()
            profile = session_profile()
            with session_proxy(profile, "http") as (_, relay):
                return containers.agent(command, sys.argv[3:], relay, profile.hostname, profile.timezone)
        except (containers.DockerError, Problem) as error:
            raise Problem(f"{error} Legacy launch: agent-proxied --legacy {command}") from None
    if mode == "firefox":
        try:
            firefox_session(sys.argv[2], sys.argv[3] == "import")
        except (Problem, containers.DockerError, OSError) as error:
            print(clean(error), flush=True)
            return 1
        return 0
    if mode == "tui":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise Problem("The picker needs a terminal. Use agent-proxied --check for text output.")
        try:
            import curses
        except ImportError:
            raise Problem("Install Python 3 with curses support for the TUI. Use --check for text diagnostics.")
        try:
            curses.wrapper(dashboard)
        except curses.error:
            raise Problem("Cannot draw the terminal. Check TERM and terminal size, or run --check.")
        return 0
    try:
        port = int(os.environ.get("PROXY_PORT") or PIN)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise Problem("PROXY_PORT must be between 1 and 65535.")
    full = mode == "check"
    rows = diagnostics(port, full, sys.argv[2] if len(sys.argv) > 2 else None)
    for row in rows:
        if full or row.state == "FAIL":
            print(f"{row.state:4}  {row.label}", file=sys.stdout if full else sys.stderr)
            if row.repair:
                print("      " + row.repair, file=sys.stdout if full else sys.stderr)
    return int(any(r.state == "FAIL" for r in rows))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Problem, containers.DockerError, OSError) as error:
        sys.exit(clean(error))
    except KeyboardInterrupt:
        sys.exit(130)
