"""Host-side rootless Docker setup, mounts and per-session relay."""
import configparser
import csv
import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ASSETS = Path(__file__).resolve().parent
AGENTS = "agent-proxied-agents:1"
BROWSER = "agent-proxied-firefox:1"
INSTALL = ("Install Docker Engine on Fedora: sudo dnf config-manager addrepo --from-repofile "
           "https://download.docker.com/linux/fedora/docker-ce.repo ; sudo dnf install docker-ce "
           "docker-ce-cli containerd.io docker-buildx-plugin docker-ce-rootless-extras ; "
           "dockerd-rootless-setuptool.sh install")



class DockerError(ValueError):
    pass


def validate_timezone(zone):
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise DockerError("Choose an installed IANA timezone such as Europe/Berlin.") from None


def docker():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    return ["docker", "--host", "unix://" + str(runtime / "docker.sock")]


def probe(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def require_engine():
    if not shutil.which("docker"):
        raise DockerError(INSTALL)
    security = probe([*docker(), "info", "--format", "{{json .SecurityOptions}}"])
    if not security:
        raise DockerError("Rootless Docker is unavailable. Run dockerd-rootless-setuptool.sh install, then systemctl --user start docker.")
    if not any("name=rootless" in item for item in json.loads(security)):
        raise DockerError("The user Docker socket must point to a rootless Docker Engine.")


def require_controllers():
    fields = "{{.CgroupVersion}} {{.CgroupDriver}} {{.CPUSet}} {{.MemoryLimit}}"
    info = probe([*docker(), "info", "--format", fields]).split()
    if len(info) != 4 or info[:2] != ["2", "systemd"]:
        raise DockerError("Rootless Docker needs cgroup v2 and the systemd cgroup driver for resource limits.")
    missing = [name for name, enabled in zip(("cpuset", "memory"), info[2:]) if enabled != "true"]
    if missing:
        raise DockerError("Delegate the " + ", ".join(missing) + " controller to the rootless Docker user service. See the setup guide.")


def require_image(image):
    label = '{{index .Config.Labels "org.agent-proxied.resource-check"}}'
    if probe([*docker(), "image", "inspect", "-f", label, image]) != "1":
        raise DockerError(f"Missing or outdated {image}. Run agent-proxied --setup-docker.")


def local_copy(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def setup():
    require_engine()
    paths = {name: shutil.which(name) for name in ("codex", "claude")}
    if not all(paths.values()):
        raise DockerError("Install the native Linux Codex and Claude Code CLIs before building the agent image.")
    with tempfile.TemporaryDirectory(prefix="agent-images-") as tmp:
        ctx = Path(tmp)
        for file in ("Dockerfile", "entrypoint.py"):
            shutil.copy(ASSETS / file, ctx / file)
        codex = Path(paths["codex"]).resolve()
        for binary in (codex, Path(paths["claude"]).resolve()):
            with binary.open("rb") as f:
                if f.read(4) != b"\x7fELF":
                    raise DockerError("Setup expects native Linux CLI executables (ELF).")
        dest = ctx / "codex-release"
        (dest / "bin").mkdir(parents=True)
        local_copy(codex, dest / "bin/codex")
        for helper in ("codex-code-mode-host", "codex-exec-server"):
            if (codex.parent / helper).exists():
                local_copy(codex.parent / helper, dest / "bin" / helper)
        manifest = codex.parent.parent / "codex-package.json"
        if manifest.is_file():
            local_copy(manifest, dest / manifest.name)
        for name in ("codex-path", "codex-resources"):
            source = codex.parent.parent / name
            if source.is_dir():
                shutil.copytree(source, dest / name, copy_function=local_copy)
        local_copy(Path(paths["claude"]).resolve(), ctx / "claude")
        for target, image in (("agents", AGENTS), ("browser", BROWSER)):
            args = [*docker(), "build", "--pull", "--target", target, "-t", image, str(ctx)]
            if subprocess.call(args):
                raise DockerError("Docker image build failed. See the build output above.")


def bind(source, dest, readonly=False):
    # Docker --mount uses CSV, including for paths containing commas.
    output = io.StringIO()
    fields = ["type=bind", f"source={source}", f"target={dest}"]
    csv.writer(output, lineterminator="\n").writerow(fields + (["readonly"] if readonly else []))
    return ["--mount", output.getvalue().rstrip("\n")]


def workspace():
    cwd = Path.cwd()
    if cwd == Path("/"):
        raise DockerError("Enter your home or a project directory before launching an agent.")
    if cwd == Path.home().resolve():
        return [*bind(cwd, "/workspace"), "-w", "/workspace"]
    top = probe(["git", "rev-parse", "--show-toplevel"])
    root = Path(top).resolve() if top else cwd
    if root == Path("/") or root == Path.home() or root in Path.home().parents:
        raise DockerError("Choose a checkout that does not contain your home directory.")
    mounts = bind(root, "/workspace")
    if (root / ".git").is_file():
        common = probe(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])
        gitdir = probe(["git", "rev-parse", "--absolute-git-dir"])
        if not common or not gitdir or not Path(gitdir).is_relative_to(Path(common)):
            raise DockerError("Cannot resolve this checkout's Git metadata.")
        common = Path(common).resolve()
        if common == Path.home() or common in Path.home().parents:
            raise DockerError("The Git metadata directory contains the home directory.")
        mounts += bind(common, "/run/git-common")
        inner = Path("/run/git-common") / Path(gitdir).resolve().relative_to(common)
        mounts += ["-e", f"GIT_DIR={inner}", "-e", "GIT_WORK_TREE=/workspace"]
    return [*mounts, "-w", str(Path("/workspace") / cwd.relative_to(root))]


@dataclass(frozen=True)
class ResourceLimits:
    cpus: tuple
    cores: int
    single_core: bool
    memory: int

    @property
    def cpu_list(self):
        return ",".join(map(str, self.cpus))

    def summary(self):
        text = (f"Per session: {self.cores} physical core(s), {len(self.cpus)} hardware thread(s), "
                f"{self.memory / (1 << 30):.2f} GiB RAM")
        return text + (" (only one core available; kept for the session)" if self.single_core else "")


def resource_limits():
    # Linux gives siblings of a physical core the same canonical CPU list,
    # including when only part of the core is in our scheduling affinity.
    groups = {}
    try:
        for cpu in sorted(os.sched_getaffinity(0)):
            topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
            siblings = topology.read_text().strip()
            if not siblings:
                raise DockerError(f"Missing physical core topology for CPU {cpu}.")
            groups.setdefault(siblings, []).append(cpu)
        match = re.search(r"^MemTotal:\s+(\d+) kB$", Path("/proc/meminfo").read_text(), re.M)
    except OSError as error:
        raise DockerError(f"Cannot read CPU topology or installed memory: {error}") from error
    if not match or not groups:
        raise DockerError("Cannot determine installed RAM or available physical cores.")
    memory = int(match[1]) * 1024 - (1 << 30)
    if memory < 6 * (1 << 20):
        raise DockerError("Reserving 1 GiB would leave less than Docker's 6 MiB minimum.")
    single = len(groups) == 1
    if not single:
        # Reserve the core with the highest first available CPU ID on every launch.
        groups.popitem()
    cpus = tuple(sorted(cpu for group in groups.values() for cpu in group))
    return ResourceLimits(cpus, len(groups), single, memory)


def container_args(name, host, volume, zone):
    validate_timezone(zone)
    limits = resource_limits()
    return [*docker(), "run", "--pull=never", "--rm", "--init", "--name", name,
            "--cgroupns", "private", "--cpuset-cpus", limits.cpu_list,
            "--memory", str(limits.memory), "--memory-swap", "-1",
            "-e", f"AGENT_EXPECT_CPUS={limits.cpu_list}", "-e", f"AGENT_EXPECT_RAM={limits.memory}",
            "--network", "none", "--hostname", host, "--user", "0:0", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--security-opt", "label=disable",
            "-v", f"{volume}:/home/agent", "-e", f"TZ={zone}",
            "-e", "LANG=en_US.UTF-8", "-e", "LC_ALL=en_US.UTF-8", "-e", "BROWSER_LANGUAGE=en-US"]


def remove(name):
    subprocess.run([*docker(), "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def agent(command, args, sock, hostname, timezone):
    mounts = workspace()
    require_image(AGENTS)
    name = "agent-session-" + uuid.uuid4().hex
    options = container_args(name, hostname, "agent-proxied-home-" + command, timezone)
    options += ["-i"]
    if os.isatty(0) and os.isatty(1):
        options += ["-t", "-e", "TERM=" + os.environ.get("TERM", "xterm-256color")]
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        options += ["-e", key + "=http://127.0.0.1:2080"]
    for key in ("NO_PROXY", "no_proxy"):
        options += ["-e", key + "=localhost,127.0.0.1,::1"]
    try:
        return subprocess.call([*options, *mounts, *sock, AGENTS, command, *args])
    finally:
        remove(name)


def wayland():
    display = os.environ.get("WAYLAND_DISPLAY")
    if not display:
        raise DockerError("Open the picker in a Wayland desktop session.")
    path = Path(display)
    if not path.is_absolute():
        path = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / path
    if not path.is_socket():
        raise DockerError("The Wayland display socket is unavailable. Open the picker on your desktop.")
    return [*bind(path, "/run/wayland/wayland-0", True), "-e", "WAYLAND_DISPLAY=wayland-0",
            "-e", "XDG_RUNTIME_DIR=/run/wayland", "-e", "MOZ_ENABLE_WAYLAND=1"]


@contextmanager
def firefox_profile_lock(source):
    parentlock = source / ".parentlock"
    if not parentlock.is_file():
        yield
        return
    with parentlock.open("r+b") as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DockerError("Close Firefox before copying the profile.") from None
        yield


def import_profile(source, volume):
    # Only migration sees the old profile. The browser itself receives just its volume.
    def version(value):
        match = re.search(r"\d+(?:\.\d+)*", value)
        if not match:
            raise DockerError("Cannot determine Firefox profile compatibility.")
        return tuple(map(int, match[0].split(".")))

    base = [*docker(), "run", "--rm", "--pull=never", "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--security-opt", "label=disable", "-v", f"{volume}:/data"]
    compat = source / "compatibility.ini"
    if compat.is_file():
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(compat)
        built = probe([*base, "--entrypoint", "/usr/bin/firefox", BROWSER, "--version"])
        if version(built) < version(ini.get("Compatibility", "LastVersion", fallback="")):
            raise DockerError("Update the Firefox image with --setup-docker before importing this newer profile.")
    copy = """
set -eu
test ! -d /data/profile || exit 0
stage=/data/import-stage
rm -rf "$stage"
mkdir -m 700 "$stage"
tar -C /source --exclude=./cache --exclude=./user.js --exclude=./.session.lock --exclude=./.parentlock --exclude=./lock -cf - . |
    tar --no-same-owner -C "$stage" -xf -
mv "$stage" /data/profile
"""
    with firefox_profile_lock(source):
        result = subprocess.run([*base, *bind(source, "/source", True), "--entrypoint", "/bin/bash",
                                 BROWSER, "-o", "pipefail", "-c", copy], stdout=subprocess.DEVNULL)
    if result.returncode:
        raise DockerError("Profile copy failed. The original is intact; retry the import.")
