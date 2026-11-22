"""Run with python3 -B -m unittest discover -s tests."""
import contextlib
import runpy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ASSETS = Path(__file__).resolve().parents[1] / ".local/share/agent-proxied"
sys.path.insert(0, str(ASSETS))
import docker_runtime as runtime


def git(directory, *args, env=None):
    return subprocess.check_output(["git", "-C", directory, *args], text=True, env=env).strip()


class WorkspaceTests(unittest.TestCase):
    def test_checkout_and_linked_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "main checkout"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "-qm", "initial", "--allow-empty")
            linked = Path(tmp) / "linked checkout"
            git(repo, "worktree", "add", "-qb", "work", str(linked))
            for root in (repo, linked):
                sub = root / "src"
                sub.mkdir()
                with contextlib.chdir(sub):
                    args = runtime.workspace()
                self.assertIn(f"type=bind,source={root},target=/workspace", args)
                self.assertEqual(args[-2:], ["-w", "/workspace/src"])
                env = os.environ.copy()
                if root == linked:
                    self.assertIn(f"type=bind,source={repo}/.git,target=/run/git-common", args)
                    inner = next(x for x in args if x.startswith("GIT_DIR="))
                    env["GIT_DIR"] = inner.split("=", 1)[1].replace("/run/git-common", str(repo / ".git"))
                    env["GIT_WORK_TREE"] = str(root)
                (sub / "feature").write_text("change")
                self.assertEqual(git(sub, "ls-files", "--others", "--exclude-standard", env=env), "feature")
                git(root, "add", "src/feature")
                git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "feature")
                self.assertEqual(git(sub, "show", "HEAD:src/feature", env=env), "change")

    def test_launch_from_home(self):
        with contextlib.chdir(Path.home()):
            self.assertEqual(runtime.workspace(), [
                *runtime.bind(Path.cwd(), "/workspace"), "-w", "/workspace"])

    def test_filesystem_root(self):
        with contextlib.chdir("/"), self.assertRaises(runtime.DockerError):
            runtime.workspace()

    def test_container_contract(self):
        args = runtime.container_args("session", "agent", "home", "Europe/Berlin")
        self.assertIn("none", args)
        self.assertIn("TZ=Europe/Berlin", args)
        self.assertIn("LANG=en_US.UTF-8", args)
        self.assertIn("0:0", args)
        self.assertIn("home:/home/agent", args)
        self.assertNotIn("/var/run/docker.sock", args)
        self.assertFalse(any("SSH_AUTH_SOCK" in arg for arg in args))

    def test_rootful_daemon_rejected(self):
        with patch.object(shutil, "which", return_value="/bin/docker"), patch.object(runtime, "probe", return_value='["name=seccomp"]'):
            with self.assertRaisesRegex(runtime.DockerError, "rootless"):
                runtime.require_engine()


class ResourceTests(unittest.TestCase):
    def limits(self, topology, total_kib=8 * 1024**2, available=None):
        def read(path):
            if str(path) == "/proc/meminfo":
                return f"MemTotal: {total_kib} kB\nMemAvailable: 10 kB\n"
            return topology[int(path.parents[1].name[3:])]
        with patch.object(os, "sched_getaffinity", return_value=set(topology if available is None else available)), patch.object(Path, "read_text", read):
            return runtime.resource_limits()

    def test_smt_and_restricted_sparse_affinity(self):
        topo = {0: "0,8", 8: "0,8", 2: "2,10", 10: "2,10", 5: "5,13", 13: "5,13"}
        full = self.limits(topo)
        self.assertEqual((full.cpus, full.cores, full.memory), ((0, 2, 8, 10), 2, 7 << 30))
        restricted = self.limits(topo, available=[2, 5, 10])
        self.assertEqual(restricted.cpus, (2, 10))
        self.assertFalse(restricted.single_core)

    def test_no_smt_or_only_one_core(self):
        self.assertEqual(self.limits({1: "1", 7: "7"}).cpus, (1,))
        only = self.limits({3: "3,11", 11: "3,11"})
        self.assertTrue(only.single_core)
        self.assertEqual(only.cpus, (3, 11))

    def test_memory_floor(self):
        with self.assertRaisesRegex(runtime.DockerError, "6 MiB"):
            self.limits({1: "1"}, total_kib=1024**2)
        self.assertEqual(self.limits({1: "1"}, total_kib=1024**2 + 6144).memory, 6 << 20)

    def test_missing_delegation(self):
        for info, message in (("1 none false false", "cgroup v2"), ("2 systemd false true", "cpuset"),
                              ("2 systemd true false", "memory")):
            with patch.object(runtime, "probe", return_value=info), self.assertRaisesRegex(runtime.DockerError, message):
                runtime.require_controllers()

    def test_unenforced_limits_stop_entrypoint(self):
        env = {"AGENT_EXPECT_CPUS": "2,4", "AGENT_EXPECT_RAM": "12345678"}
        with patch.dict(os.environ, env), patch.object(os, "sched_getaffinity", return_value={2, 4, 8}):
            with self.assertRaisesRegex(SystemExit, "affinity"):
                runpy.run_path(str(ASSETS / "entrypoint.py"))
        with patch.dict(os.environ, env), patch.object(os, "sched_getaffinity", return_value={2, 4}), patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_text", return_value="max"):
            with self.assertRaisesRegex(SystemExit, "RAM limit"):
                runpy.run_path(str(ASSETS / "entrypoint.py"))


class MigrationTests(unittest.TestCase):
    def test_copy_retry_and_preserve_existing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            data = Path(tmp) / "data"
            data.mkdir()
            source.mkdir()
            (source / "cookies.sqlite").write_text("cookies")
            (source / "user.js").write_text("old host path")
            stage = data / "import-stage"
            stage.mkdir()
            (stage / "partial").touch()
            real_run = subprocess.run
            def simulate_mounts(args, **kwargs):
                cmd = args[-1].replace("/data", str(data)).replace("/source", str(source))
                return real_run(["bash", "-o", "pipefail", "-c", cmd], **kwargs)
            with patch.object(subprocess, "run", side_effect=simulate_mounts):
                runtime.import_profile(source, "fixture")
                copied = data / "profile"
                self.assertEqual((copied / "cookies.sqlite").read_text(), "cookies")
                self.assertFalse((copied / "user.js").exists())
                self.assertFalse((copied / "partial").exists())
                (copied / "cookies.sqlite").write_text("new cookies")
                runtime.import_profile(source, "fixture")
                self.assertEqual((copied / "cookies.sqlite").read_text(), "new cookies")
            self.assertEqual((source / "cookies.sqlite").read_text(), "cookies")

    def test_reject_older_browser_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "compatibility.ini").write_text("[Compatibility]\nLastVersion=157.0_20261101/20261101\n")
            with patch.object(runtime, "probe", return_value="Mozilla Firefox 156.0"), self.assertRaises(runtime.DockerError):
                runtime.import_profile(src, "fixture")


class DockerIntegration(unittest.TestCase):
    def test_live_rootless_agent_image(self):
        """Runs automatically on a host with the built rootless Docker image."""
        try:
            runtime.require_engine()
            runtime.require_image(runtime.AGENTS)
        except runtime.DockerError as error:
            self.skipTest(str(error))
        import socketserver
        import threading
        import uuid

        class Reply(socketserver.BaseRequestHandler):
            def handle(self):
                if self.request.recv(64):
                    self.request.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 13\r\n\r\nproxy fixture")

        volume = "agent-test-" + uuid.uuid4().hex
        try:
            with tempfile.TemporaryDirectory() as tmp, socketserver.TCPServer(("127.0.0.1", 0), Reply) as server:
                threading.Thread(target=server.serve_forever, daemon=True).start()
                manager = runpy.run_path(str(ASSETS.parents[1] / "bin/agent-proxy-manager.py"))
                manager["session_proxy"].__globals__["ROOT"] = Path(tmp)
                profile = manager["SessionProfile"]("fixture", {"protocol": "freedom"}, "Europe/Berlin", "workstation-test")
                with manager["session_proxy"](profile, "http") as (_, relay):
                    for attempt in range(2):
                        name = volume + str(attempt)
                        options = runtime.container_args(name, profile.hostname, volume, profile.timezone)
                        script = """
import locale, os, socket, sys, time
from pathlib import Path
assert socket.gethostname() == 'workstation-test'
assert time.tzname[0] == 'CET'
assert locale.setlocale(locale.LC_ALL, '').startswith('en_US')
assert not os.environ.get('SSH_AUTH_SOCK')
assert not Path('/var/run/docker.sock').exists()
assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
assert Path('/home/agent/previous').exists() == (sys.argv[1] == '1')
Path('/home/agent/previous').touch()
Path('/workspace/edited').write_text('saved')
import http.client
proxy = http.client.HTTPConnection('127.0.0.1', 2080, timeout=3)
proxy.request('GET', 'http://127.0.0.1:' + sys.argv[2] + '/')
assert proxy.getresponse().read() == b'proxy fixture'
"""
                        subprocess.run([*options, *relay, *runtime.bind(tmp, "/workspace"),
                                        runtime.AGENTS, "python3", "-c", script, str(attempt), str(server.server_address[1])], check=True)
                # The upstream is still alive, but there is no Unix relay in this session.
                options = runtime.container_args(volume + "offline", "agent", volume, "UTC")
                offline = """
import socket
try:
    with socket.create_connection(('127.0.0.1', 2080), timeout=3) as proxy:
        proxy.sendall(b'test')
        assert not proxy.recv(32)
except (ConnectionError, TimeoutError):
    pass
try:
    socket.create_connection(('192.0.2.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('Unexpected direct network access')
"""
                subprocess.run([*options, runtime.AGENTS, "python3", "-c", offline], check=True)
                self.assertEqual((Path(tmp) / "edited").stat().st_uid, os.getuid())
                server.shutdown()
        finally:
            subprocess.run([*runtime.docker(), "volume", "rm", volume], stdout=subprocess.DEVNULL)
