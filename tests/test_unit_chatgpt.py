"""Offline desktop/Codex compatibility checks. No login, network, or app config writes."""
import contextlib
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import setup as SETUP
import server


class Registration(unittest.TestCase):
    def test_toml_round_trip_for_difficult_paths(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("TOML parser requires Python 3.11+; server supports 3.9+")
        for folder in ('D:\\MCP tools\\Rental "test"', '/tmp/rental $x `test`', "/tmp/owner's folder", '/tmp/\u79df\u91d1'):
            with self.subTest(folder=folder), mock.patch.object(SETUP, "HERE", folder):
                entry = tomllib.loads(SETUP.chatgpt_toml())["mcp_servers"]["rentmanager"]
                self.assertEqual(entry["args"], [os.path.join(folder, "server.py")])
                self.assertEqual(entry["cwd"], folder)
                self.assertEqual(entry["command"], sys.executable)
                self.assertEqual(entry["tool_timeout_sec"], 180)
                self.assertNotIn("env", entry)

    def test_powershell_arguments_are_literal(self):
        with mock.patch.object(SETUP.os, "name", "nt"):
            self.assertEqual(SETUP.shell_arg("D:\\MCP $x `test`\\owner's python.exe"),
                             "'D:\\MCP $x `test`\\owner''s python.exe'")

    def test_posix_arguments_round_trip(self):
        import shlex
        value = "/tmp/MCP $x `test`/owner's python"
        with mock.patch.object(SETUP.os, "name", "posix"):
            self.assertEqual(shlex.split(SETUP.shell_arg(value)), [value])

    def test_printing_needs_no_credentials_and_writes_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(SETUP, "read_env", side_effect=AssertionError("read credentials")), \
             mock.patch.object(SETUP, "auth_test", side_effect=AssertionError("login")), \
             mock.patch("builtins.open", side_effect=AssertionError("file access")), \
             mock.patch.object(SETUP.subprocess, "run", side_effect=AssertionError("launch")):
            SETUP.print_chatgpt_config()
        for text in ("STDIO", "codex mcp add rentmanager", "[mcp_servers.rentmanager]", "tool_timeout_sec = 180"):
            self.assertIn(text, out.getvalue())

    def test_cli_print_only_does_not_start_setup(self):
        result = subprocess.run([sys.executable, "-B", os.path.join(HERE, "setup.py"), "--print-chatgpt-config"],
                                cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", timeout=15,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[mcp_servers.rentmanager]", result.stdout)
        self.assertNotIn("Step 1/5", result.stdout)


class WireClient:
    def __init__(self, folder):
        env = {k: v for k, v in os.environ.items() if not k.startswith("RM_")}
        env.update(RM_OUT=folder, RM_OUT_KEEP_DAYS="0", RM_MCP_LOG="off", PYTHONDONTWRITEBYTECODE="1")
        # Desktop apps need not start in the repository's directory.
        self.process = subprocess.Popen([sys.executable, "-B", os.path.join(HERE, "server.py")], cwd=folder,
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.messages = queue.Queue()
        self.stderr = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self.stderr.extend(self.process.stderr), daemon=True).start()
        self.n = 0

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(json.loads(line))

    def write(self, value):
        self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.n += 1
        self.write(dict(jsonrpc="2.0", id=self.n, method=method, params=params or {}))
        result = self.messages.get(timeout=15)
        if result.get("id") != self.n:
            raise AssertionError("Unexpected reply (including reply to a notification): %r" % result)
        return result

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            raise
        finally:
            self.process.stdout.close()
            self.process.stderr.close()


class DesktopWire(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.client = WireClient(self.folder.name)

    def tearDown(self):
        try:
            self.client.close()
        finally:
            self.folder.cleanup()

    def initialize(self, version="2025-06-18"):
        return self.client.request("initialize", {"protocolVersion": version, "capabilities": {},
                                                   "clientInfo": {"name": "desktop-compatibility-check", "version": "1"}})["result"]

    def test_handshake_and_all_tools_from_an_unrelated_working_directory(self):
        init = self.initialize()
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"]["name"], "rentmanager-readonly")
        for rule in ("read-only", "passwords", "Scope", "do no arithmetic", "bulk", "local"):
            self.assertIn(rule, init["instructions"][:512])
        self.client.write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(self.client.request("ping")["result"], {})
        tools = self.client.request("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {t[0] for t in server.TOOLS})
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_protocol_negotiation_for_supported_and_newer_clients(self):
        for requested, expected in (("2024-11-05", "2024-11-05"), ("2025-03-26", "2025-03-26"), ("2099-01-01", "2025-06-18")):
            self.assertEqual(self.initialize(requested)["protocolVersion"], expected)

    def test_recipe_lookup_returns_text_without_credentials(self):
        self.initialize()
        result = self.client.request("tools/call", {"name": "find_recipe", "arguments": {"query": "vacant units"}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertIn("vacant_units", result["content"][0]["text"])

    def test_tool_errors_do_not_break_connection(self):
        self.initialize()
        for params in ({"name": "not_a_tool"}, {"name": "find_recipe", "arguments": {"query": "vacancy", "bad_argument": True}}):
            result = self.client.request("tools/call", params)["result"]
            self.assertTrue(result["isError"])
            self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(self.client.request("ping")["result"], {})

    def test_parse_error_is_recoverable_and_unadvertised_lists_are_empty(self):
        self.client.process.stdin.write(b"not json\n")
        self.client.process.stdin.flush()
        self.assertEqual(self.client.messages.get(timeout=15)["error"]["code"], -32700)
        for method, key in (("resources/list", "resources"), ("prompts/list", "prompts")):
            self.assertEqual(self.client.request(method)["result"][key], [])
        self.assertEqual(self.client.request("unsupported/method")["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
