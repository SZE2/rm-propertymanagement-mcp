#!/usr/bin/env python3
"""Offline unit tests for v2 item 4 (timeout, token cache, live property retry). No network, no credentials.
Run: python3 -m unittest discover -s tests -p "test_unit*.py"
"""
import json, os, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import rm, server


class Timeout(unittest.TestCase):
    def test_default_90(self):
        self.assertEqual(rm.DEFAULT_TIMEOUT, 90.0)

    def test_req_uses_default_and_explicit(self):
        seen = []
        class Resp:
            status = 200; headers = {}
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake(r, timeout=None): seen.append(timeout); return Resp()
        with mock.patch.dict(rm.ENV, {"RM_COMPANY": "x"}), mock.patch("urllib.request.urlopen", fake):
            rm._req("GET", "/a"); rm._req("GET", "/a", timeout=10)
        self.assertEqual(seen, [90.0, 10])

    def test_server_timeout_message(self):
        with mock.patch.object(rm, "get", side_effect=OSError("timed out")):
            with self.assertRaises(server.ToolError) as c:
                server.api("/X")
            self.assertIn("did not answer", str(c.exception))
            with self.assertRaises(server.ToolError) as c:
                server.api("/X", timeout=10)
            self.assertIn("add a date or PropertyID", str(c.exception))


class TokenCache(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = os.path.join(self.d, "tok.json")
        self.p = mock.patch.object(rm, "TOKEN_FILE", self.f); self.p.start()

    def tearDown(self):
        self.p.stop()
        for n in os.listdir(self.d): os.remove(os.path.join(self.d, n))
        os.rmdir(self.d)

    def test_atomic_write_leaves_no_temp(self):
        rm._write_token({"token": "t", "ts": 1.0})
        self.assertEqual(os.listdir(self.d), ["tok.json"])
        self.assertEqual(json.load(open(self.f))["token"], "t")

    def test_touch_is_throttled(self):
        now = time.time()
        rm._write_token({"token": "t", "ts": now - 5})
        rm._touch_token()
        self.assertEqual(json.load(open(self.f))["ts"], now - 5)  # too recent: untouched

    def test_touch_refreshes_when_old(self):
        old = time.time() - 300
        rm._write_token({"token": "t", "ts": old})
        rm._touch_token()
        self.assertGreater(json.load(open(self.f))["ts"], old + 200)

    def test_touch_missing_file_is_silent(self):
        rm._touch_token()  # must not raise


PROPS = [{"id": 1, "name": "Oak Court", "short": "OAK", "active": True, "aliases": []}]
LIVE = PROPS + [{"id": 2, "name": "Birchwood Terrace", "short": "BIRCH", "active": True, "aliases": []}]


class PropertyRetry(unittest.TestCase):
    def test_unknown_name_retries_live(self):
        with mock.patch.object(server, "properties", return_value=list(PROPS)), \
             mock.patch.object(server, "_live_properties", return_value=[dict(p) for p in LIVE]) as live:
            ids, label = server.resolve_property_ids("Birchwood Terrace")
        self.assertEqual(ids, [2]); live.assert_called_once()

    def test_unknown_id_retries_live(self):
        with mock.patch.object(server, "properties", return_value=list(PROPS)), \
             mock.patch.object(server, "_live_properties", return_value=[dict(p) for p in LIVE]):
            self.assertEqual(server.resolve_property_ids("2")[0], [2])

    def test_known_name_no_live_call(self):
        with mock.patch.object(server, "properties", return_value=list(PROPS)), \
             mock.patch.object(server, "_live_properties") as live:
            self.assertEqual(server.resolve_property_ids("Oak Court")[0], [1])
        live.assert_not_called()

    def test_still_unknown_raises_after_one_retry(self):
        with mock.patch.object(server, "properties", return_value=list(PROPS)), \
             mock.patch.object(server, "_live_properties", return_value=[dict(p) for p in LIVE]) as live:
            with self.assertRaises(server.ToolError):
                server.resolve_property_ids("Zzzqqq Nowhere")
        self.assertEqual(live.call_count, 1)


if __name__ == "__main__":
    unittest.main()
