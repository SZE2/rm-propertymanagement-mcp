"""Offline Hermes Agent registration checks. No login, network, or config writes."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import setup as SETUP


class HermesRegistration(unittest.TestCase):
    def test_entry_has_no_credentials_and_long_timeouts(self):
        entry = SETUP.hermes_entry()
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(entry["args"], [os.path.join(SETUP.HERE, "server.py")])
        self.assertEqual(entry["timeout"], 180)
        self.assertEqual(entry["connect_timeout"], 60)
        self.assertNotIn("env", entry)

    def test_yaml_round_trip_for_difficult_paths(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed; server itself is stdlib-only")
        for folder in ('D:\\MCP tools\\Rental "test"', "/tmp/rental $x `test`", "/tmp/owner's folder", "/tmp/\u79df\u91d1"):
            with self.subTest(folder=folder), mock.patch.object(SETUP, "HERE", folder):
                entry = yaml.safe_load(SETUP.hermes_yaml())["mcp_servers"]["rentmanager"]
                self.assertEqual(entry["command"], sys.executable)
                self.assertEqual(entry["args"], [os.path.join(folder, "server.py")])
                self.assertEqual(entry["timeout"], 180)
                self.assertEqual(entry["connect_timeout"], 60)
                self.assertEqual(entry["sampling"], {"enabled": False})

    def test_printing_needs_no_credentials_and_writes_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             mock.patch.object(SETUP, "read_env", side_effect=AssertionError("read credentials")), \
             mock.patch.object(SETUP, "auth_test", side_effect=AssertionError("login")), \
             mock.patch("builtins.open", side_effect=AssertionError("file access")), \
             mock.patch.object(SETUP.subprocess, "run", side_effect=AssertionError("launch")):
            SETUP.print_hermes_config()
        for text in ("hermes mcp add rentmanager --command", "mcp_servers:", "timeout: 180", "hermes mcp test rentmanager"):
            self.assertIn(text, out.getvalue())

    def test_cli_flag_prints_only(self):
        result = subprocess.run([sys.executable, "-B", os.path.join(HERE, "setup.py"), "--print-hermes-config"],
                                cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", timeout=15,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mcp_servers:", result.stdout)
        self.assertNotIn("Step 1/5", result.stdout)

    def test_print_config_mentions_hermes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            SETUP.print_config()
        self.assertIn("Hermes Agent", out.getvalue())


if __name__ == "__main__":
    unittest.main()
