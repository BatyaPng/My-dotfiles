import concurrent.futures
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

MANAGER = Path(__file__).resolve().parents[1] / ".local/bin/agent-proxy-manager.py"


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.m = runpy.run_path(str(MANAGER))["snapshot"].__globals__
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.m.update(ROOT=self.root, STORE=self.root / "profiles.json", CONFIG=self.root / "config.json")
        self.nodes = {name: {"protocol": "freedom", "settings": {"redirect": f"127.0.0.1:{port}"}}
                      for name, port in (("A", 10001), ("B", 10002))}
        self.m["save"](self.m["STORE"], self.nodes)
        self.m["save"](self.m["CONFIG"], {"outbounds": [self.nodes["A"]]})

    def test_identity_migration_and_missing_timezone(self):
        self.m["save"](self.root / "regions.json", {"A": ["Europe/Berlin", "de-DE"]})
        a = self.m["snapshot"]()
        self.assertEqual(a.timezone, "Europe/Berlin")
        args = self.m["containers"].container_args("test", a.hostname, "volume", a.timezone)
        for env in ("LANG=en_US.UTF-8", "LC_ALL=en_US.UTF-8", "BROWSER_LANGUAGE=en-US"):
            self.assertIn(env, args)
        with patch("sys.stdin.isatty", return_value=False), self.assertRaisesRegex(ValueError, "Timezone required"):
            self.m["session_profile"]("B")
        self.m["change_region"]("B", "America/New_York", "office-b")
        self.assertEqual(self.m["snapshot"]("B").hostname, "office-b")
        self.assertEqual(a, self.m["snapshot"]("A"))
        for zone, host in (("Europe/Unknown", "office"), ("UTC", "-invalid")):
            with self.assertRaises(ValueError):
                self.m["change_region"]("B", zone, host)

    def test_delete_profile(self):
        self.m["change_region"]("B", "UTC", "office-b")
        with self.assertRaisesRegex(ValueError, "another default"):
            self.m["delete_profile"]("A", self.nodes["A"])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.m["delete_profile"]("B", self.nodes["A"])
        self.m["delete_profile"]("B", self.nodes["B"])
        self.assertEqual(self.m["profiles"](), {"A": self.nodes["A"]})
        self.assertNotIn("B", self.m["regions"]())
        self.assertEqual(self.m["snapshot"]().name, "A")

    def test_duplicate_nodes_keep_selected_identity(self):
        self.m["save"](self.m["STORE"], {"A": self.nodes["A"], "B": self.nodes["A"]})
        with patch.dict(self.m, listening=lambda _: True, service=lambda *_: True):
            self.m["activate"]("B", self.nodes["A"])
        self.assertEqual(self.m["snapshot"]().name, "B")

    def test_atomic_snapshot_under_switching(self):
        for name, zone in (("A", "Europe/Berlin"), ("B", "America/New_York")):
            self.m["change_region"](name, zone, "office-" + name.lower())
        profiles = [self.m["snapshot"](name) for name in self.nodes]
        def switches():
            for _ in range(8):
                for before, after in (("A", "B"), ("B", "A")):
                    self.m["activate"](after, self.nodes[before])
        with patch.dict(self.m, validate=lambda _: None, ready=lambda _: True):
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(switches)
                for _ in range(40):
                    self.assertIn(self.m["snapshot"](), profiles)
                future.result()

    def test_independent_outbounds_mounts_and_cleanup(self):
        configs = []
        processes = []
        def spawn(args, **kwargs):
            if args[0] == "socat":
                self.assertEqual(args[-1], "TCP:127.0.0.1:31415")
            else:
                configs.append(json.loads(Path(args[-1]).read_bytes()))
            proc = MagicMock()
            proc.poll.return_value = None
            processes.append(proc)
            return proc
        with patch.dict(self.m, validate=lambda _: None, loopback_port=lambda: 31415, socks_ready=lambda _: True), patch("subprocess.Popen", side_effect=spawn), patch.object(Path, "is_socket", return_value=True):
            with self.m["session_proxy"](self.m["snapshot"]("A"), "http") as (_, left):
                with self.m["session_proxy"](self.m["snapshot"]("B"), "socks") as (_, right):
                    self.assertNotEqual(left, right)
                    self.assertEqual([cfg["outbounds"][0] for cfg in configs], list(self.nodes.values()))
                    self.assertEqual([cfg["inbounds"][0]["protocol"] for cfg in configs], ["http","socks"])
                    self.assertNotEqual(configs[0]["inbounds"][0]["listen"], configs[1]["inbounds"][0]["listen"])
                processes[0].terminate.assert_not_called()
                processes[1].terminate.assert_called_once()
                processes[2].terminate.assert_called_once()
            processes[0].terminate.assert_called_once()

    def test_firefox_socks_handshake(self):
        import socket
        import shutil
        if not self.m["XRAY"].is_file() or not shutil.which("socat"):
            self.skipTest("Install Xray and socat for the live Firefox listener test.")
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.bind(str(self.root / "permission-probe"))
        except PermissionError:
            self.skipTest("Unix socket creation denied in this environment.")
        with self.m["session_proxy"](self.m["snapshot"]("A"), "socks") as (_, mount):
            directory = mount[1].split("source=", 1)[1].split(",target=", 1)[0]
            self.assertTrue(self.m["socks_ready"](Path(directory) / "proxy.sock"))

    def test_live_routes_until_their_own_socket_closes(self):
        import contextlib
        import http.server
        import socket
        import threading

        if not self.m["XRAY"].is_file():
            self.skipTest("Install Xray for live proxy routing tests.")
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.bind(str(self.root / "permission-probe"))
        except PermissionError:
            self.skipTest("Unix socket creation denied in this environment.")

        class Exit(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(self.server.marker.encode())

            def log_message(self, *_):
                pass

        def request(proc_cfg):
            path = proc_cfg[1][1].split("source=", 1)[1].split(",target=", 1)[0]
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(5)
                sock.connect(path + "/proxy.sock")
                sock.sendall(b"GET http://127.0.0.1:1234/ HTTP/1.0\r\nHost: 127.0.0.1:1234\r\n\r\n")
                with sock.makefile("rb") as stream:
                    return stream.read().split(b"\r\n\r\n", 1)[1]

        with contextlib.ExitStack() as servers:
            for name in self.nodes:
                service = servers.enter_context(http.server.ThreadingHTTPServer(("127.0.0.1", 0), Exit))
                service.marker = name
                threading.Thread(target=service.serve_forever, daemon=True).start()
                servers.callback(service.shutdown)
                self.nodes[name]["settings"]["redirect"] = f"127.0.0.1:{service.server_port}"
                self.m["change_region"](name, "UTC", "host-" + name.lower())
            self.m["save"](self.m["STORE"], self.nodes)
            self.m["save"](self.m["CONFIG"], {"outbounds": [self.nodes["A"]]})
            with contextlib.ExitStack() as first, contextlib.ExitStack() as second:
                a = first.enter_context(self.m["session_proxy"](self.m["snapshot"](), "http"))
                self.assertEqual(request(a), b"A")
                with patch.dict(self.m, ready=lambda _: True):
                    self.m["activate"]("B", self.nodes["A"])
                b = second.enter_context(self.m["session_proxy"](self.m["snapshot"](), "http"))
                self.assertEqual(request(a), b"A")
                self.assertEqual(request(b), b"B")
                first.close()
                with self.assertRaises(OSError):
                    request(a)
                self.assertEqual(request(b), b"B")
