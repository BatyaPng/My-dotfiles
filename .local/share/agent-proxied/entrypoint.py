"""Container entry point. Only the relay and explicitly mounted data are visible."""
import json
import locale
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# Private cgroup namespace: the root is this container's own cgroup.
expected_cpus = {int(cpu) for cpu in os.environ["AGENT_EXPECT_CPUS"].split(",")}
if os.sched_getaffinity(0) != expected_cpus:
    sys.exit("CPU affinity differs from the requested limit. Check rootless Docker cpuset delegation.")
memory_max = Path("/sys/fs/cgroup/memory.max")
if not memory_max.is_file() or memory_max.read_text().strip() != os.environ["AGENT_EXPECT_RAM"]:
    sys.exit("RAM limit differs from the requested limit. Check rootless Docker memory delegation.")

try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    sys.exit("Locale unavailable in the image: " + os.environ["LC_ALL"])
timezone = Path("/usr/share/zoneinfo") / os.environ["TZ"]
if not timezone.is_file():
    sys.exit("Install the selected timezone in the image.")
Path("/etc/localtime").unlink(missing_ok=True)
Path("/etc/localtime").symlink_to(timezone)
app, *args = sys.argv[1:]
if app == "firefox":
    home = Path.home()
    profile = home / "profile"
    profile.mkdir(mode=0o700, exist_ok=True)
    settings = {"network.proxy.type": 1, "network.proxy.socks": "127.0.0.1",
                "network.proxy.socks_port": 2080, "network.proxy.socks_version": 5,
                "network.proxy.socks_remote_dns": True, "network.proxy.no_proxies_on": "",
                "network.proxy.failover_direct": False, "network.trr.mode": 5,
                "media.peerconnection.enabled": False,
                "intl.accept_languages": os.environ["BROWSER_LANGUAGE"],
                "browser.cache.disk.parent_directory": str(home / "cache")}
    prefs = profile / "user.js"
    temp = profile / ".agent-user.tmp"
    temp.write_text("".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k,v in settings.items()))
    temp.replace(prefs)
    args = ["--no-remote", "--profile", str(profile)]
relay = subprocess.Popen(["socat", "TCP-LISTEN:2080,bind=127.0.0.1,reuseaddr,fork",
                          "UNIX-CONNECT:/run/relay/proxy.sock"])
for _ in range(50):
    if relay.poll() is not None:
        sys.exit("Container proxy listener failed to start.")
    try:
        with socket.create_connection(("127.0.0.1", 2080), timeout=.1):
            break
    except OSError:
        time.sleep(.02)
else:
    sys.exit("Timed out starting the container proxy listener.")
os.execvp(app, [app, *args])
