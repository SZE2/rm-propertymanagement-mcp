#!/usr/bin/env python3
"""Offline unit tests for v4 item 1 (save collisions, chunk property source)."""
import os, sys, json, tempfile, threading, unittest
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server


class SaveCollision(unittest.TestCase):
    def test_same_second_distinct_files(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(server, "out_dir", return_value=td), \
                 mock.patch.object(server.time, "strftime", return_value="20260101-000000"):
                paths = []
                def w(i): paths.append(server.save("lbl", {"i": i}, [{"i": i}])[0])
                ts = [threading.Thread(target=w, args=(i,)) for i in range(20)]
                [t.start() for t in ts]; [t.join() for t in ts]
            self.assertEqual(len(set(paths)), 20)
            seen = sorted(json.load(open(p, encoding="utf-8"))["i"] for p in paths)
            self.assertEqual(seen, list(range(20)))


class ChunkLive(unittest.TestCase):
    def test_properties_live_skips_file(self):
        live = [{"id": 1, "name": "a", "short": "", "active": True, "aliases": []}]
        with mock.patch.object(server, "_live_properties", return_value=live):
            self.assertEqual(server.properties(live=True), live)


if __name__ == "__main__":
    unittest.main()
