#!/usr/bin/env python3
"""Offline unit tests for v4 item 2b (rm.get retries transient failures once). Fake _req, no network."""
import os, sys, unittest, socket, urllib.error
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import rm

def run(seq):
    calls = []
    def fake(method, path, headers=None, body=None, timeout=None):
        calls.append(path)
        x = seq[min(len(calls) - 1, len(seq) - 1)]
        if isinstance(x, Exception): raise x
        return x, {}, "[]" if x == 200 else "err"
    with mock.patch.object(rm, "_req", fake), mock.patch.object(rm, "get_token", lambda force=False: "t"), \
         mock.patch.object(rm, "_throttle", lambda: None), mock.patch.object(rm, "_touch_token", lambda: None), \
         mock.patch.object(rm.time, "sleep", lambda s: None):
        try: r = rm.get("/X")
        except Exception as e: r = e
    return r, len(calls)

class Retry(unittest.TestCase):
    def test_503_then_200(self):
        r, n = run([503, 200]); self.assertEqual((r[0], n), (200, 2))
    def test_503_twice_returns_status(self):
        r, n = run([503, 503]); self.assertEqual((r[0], n), (503, 2))
    def test_502_504(self):
        for c in (502, 504): self.assertEqual(run([c, 200])[1], 2)
    def test_400_not_retried(self):
        r, n = run([400]); self.assertEqual((r[0], n), (400, 1))
    def test_reset_then_ok(self):
        r, n = run([ConnectionResetError(), 200]); self.assertEqual((r[0], n), (200, 2))
    def test_urlerror_then_ok(self):
        r, n = run([urllib.error.URLError("x"), 200]); self.assertEqual((r[0], n), (200, 2))
    def test_urlerror_twice_raises(self):
        r, n = run([urllib.error.URLError("x")]); self.assertIsInstance(r, urllib.error.URLError); self.assertEqual(n, 2)
    def test_timeout_not_retried(self):
        r, n = run([urllib.error.URLError(socket.timeout())]); self.assertIsInstance(r, urllib.error.URLError); self.assertEqual(n, 1)

if __name__ == "__main__":
    unittest.main()
