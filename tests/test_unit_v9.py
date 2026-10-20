#!/usr/bin/env python3
"""Offline tests for the setup guide work: .env quoting round trip, .env wins over the OS environment,
setup.py --help, --merge-desktop-config (temp files only, never the real config).
Run: PY tests/test_unit_v9.py   (PY = py -3 on Windows, python3 on macOS/Linux)
"""
import json, os, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import rm
import setup as SETUP

FAKE_PW = 'pa"ss word # $HOME \\x \'q\' '
FAKE_PW2 = "'lead-quote and trail-quote'"


class EnvRoundTrip(unittest.TestCase):
    def test_roundtrip(self):
        for pw in (FAKE_PW, FAKE_PW2, "plain123", '"', " spaced ", "a#b", "$x", "back\\slash"):
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, ".env")
                SETUP.write_env("co-de", "us er", pw, "", path=p)
                with mock.patch.object(rm, "HERE", d), mock.patch.dict(os.environ, {}, clear=False):
                    env = rm.load_env()
                self.assertEqual(env["RM_PASSWORD"], pw, repr(pw))
                self.assertEqual(env["RM_USERNAME"], "us er")
                self.assertEqual(env["RM_COMPANY"], "co-de")

    def test_plain_values_unquoted(self):
        self.assertEqual(rm.quote_env_value("simple-value_1"), "simple-value_1")

    def test_env_file_wins_over_os_environment(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, ".env"), "w").write("RM_COMPANY=from-file\nRM_PASSWORD=pw-file\n")
            with mock.patch.object(rm, "HERE", d), mock.patch.dict(os.environ, {"RM_COMPANY": "stale", "RM_OTHER": "kept"}):
                env = rm.load_env()
            self.assertEqual(env["RM_COMPANY"], "from-file")
            self.assertEqual(env["RM_OTHER"], "kept")

    def test_legacy_quoted_values(self):
        self.assertEqual(rm.parse_env_value('"abc"'), "abc")
        self.assertEqual(rm.parse_env_value("'abc'"), "abc")
        self.assertEqual(rm.parse_env_value("  abc  "), "abc")


class SetupCli(unittest.TestCase):
    def test_help_does_not_start_setup(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "setup.py"), "--help"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0)
        for flag in ("--non-interactive", "--print-config", "--merge-desktop-config", "--charge-types", "--force", "--aliases", "--index", "--skip-creds"):
            self.assertIn(flag, r.stdout)
        self.assertNotIn("Step 1/5", r.stdout)

    def test_print_config_full_instruction(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "setup.py"), "--print-config"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn('"rentmanager"', r.stdout)
        self.assertIn('"mcpServers"', r.stdout)
        self.assertIn("claude_desktop_config.json", r.stdout)


class MergeConfig(unittest.TestCase):
    def _run(self, content, answer="y"):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "claude_desktop_config.json")
        open(p, "w", encoding="utf-8").write(content)
        with mock.patch("builtins.print"):
            rc = SETUP.merge_desktop_config(p, confirm=lambda _: answer)
        return rc, p, d

    def test_merge_keeps_other_servers_and_backs_up(self):
        orig = json.dumps({"mcpServers": {"other": {"command": "x", "args": ["1"]}}, "theme": "dark"})
        rc, p, d = self._run(orig)
        self.assertEqual(rc, 0)
        cfg = json.load(open(p, encoding="utf-8"))
        self.assertEqual(cfg["mcpServers"]["other"], {"command": "x", "args": ["1"]})
        self.assertEqual(cfg["theme"], "dark")
        self.assertEqual(cfg["mcpServers"]["rentmanager"], SETUP.server_entry())
        self.assertEqual(open(p + ".bak", encoding="utf-8").read(), orig)

    def test_creates_mcpservers_when_missing(self):
        rc, p, d = self._run("{}")
        self.assertEqual(rc, 0)
        self.assertIn("rentmanager", json.load(open(p))["mcpServers"])

    def test_refuses_invalid_json(self):
        rc, p, d = self._run("{ not json")
        self.assertEqual(rc, 1)
        self.assertEqual(open(p).read(), "{ not json")
        self.assertFalse(os.path.exists(p + ".bak"))

    def test_declined_changes_nothing(self):
        rc, p, d = self._run("{}", answer="n")
        self.assertEqual(rc, 1)
        self.assertEqual(open(p).read(), "{}")


if __name__ == "__main__":
    unittest.main()
