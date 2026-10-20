#!/usr/bin/env python3
"""Offline unit tests for v3 item 2 (property chunking merge). No network, no credentials.
Run: python3 -m unittest discover -s tests -p "test_unit*.py"
"""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server

PROPS = [{"id": i, "active": i % 3 != 0} for i in range(1, 11)]   # 3,6,9 inactive


class Split(unittest.TestCase):
    def test_round_robin_active_only(self):
        ch = server.split_chunks(PROPS, 3)
        self.assertEqual(len(ch), 3)
        self.assertEqual(sorted(sum(ch, [])), [1, 2, 4, 5, 7, 8, 10])   # inactive 3,6,9 excluded (report default is active only)
        self.assertEqual([c[0] for c in ch], [1, 2, 4])

    def test_cap_and_small(self):
        self.assertEqual(len(server.split_chunks(PROPS, 9)), 4)
        self.assertEqual(len(server.split_chunks(PROPS[:2], 4)), 2)
        self.assertEqual(server.split_chunks(PROPS, 1), [[1, 2, 4, 5, 7, 8, 10]])


class Merge(unittest.TestCase):
    def test_only_named_grid_concatenated(self):
        a = ({"Grid1": [{"x": 1}], "Grid2": [{"s": 10}]}, "q")
        b = ({"Grid1": [{"x": 2}, {"x": 3}], "Grid2": [{"s": 20}]}, "q")
        m = server.merge_chunk_reports([a, b, ({}, "q")], "Grid1")
        self.assertEqual(m, {"Grid1": [{"x": 1}, {"x": 2}, {"x": 3}]})

    def test_empty(self):
        self.assertEqual(server.merge_chunk_reports([({}, "q")], "Grid1"), {"Grid1": []})


class Run(unittest.TestCase):
    rep = {"id": 1, "name": "R"}

    def test_parallel_merge_and_param(self):
        seen = []
        def fake(rep, p):
            seen.append(p["PropertyIDs"]); ids = [int(x) for x in p["PropertyIDs"].strip("()").split(",")]
            return {"Grid1": [{"id": i} for i in ids]}, "q"
        with mock.patch.object(server, "run_report_raw", fake):
            d, q = server.run_report_chunked(self.rep, {"AsOfDate": "2026-01-01", "PropertyIDs": ""}, "PropertyIDs", [[1, 3], [2]], "Grid1")
        self.assertEqual(sorted(r["id"] for r in d["Grid1"]), [1, 2, 3])
        self.assertEqual(sorted(seen), ["(1,3)", "(2)"])
        self.assertIn("2 chunks", q)

    def test_retry_once_then_raise(self):
        calls = {"n": 0}
        def flaky(rep, p):
            calls["n"] += 1
            if calls["n"] == 1: raise server.ToolError("boom")
            return {"Grid1": [{"id": 1}]}, "q"
        with mock.patch.object(server, "run_report_raw", flaky):
            d, _ = server.run_report_chunked(self.rep, {}, "PropertyIDs", [[1]], "Grid1")
        self.assertEqual(d["Grid1"], [{"id": 1}]); self.assertEqual(calls["n"], 2)
        def dead(rep, p): raise server.ToolError("down")
        with mock.patch.object(server, "run_report_raw", dead):
            with self.assertRaises(server.ToolError):
                server.run_report_chunked(self.rep, {}, "PropertyIDs", [[1], [2]], "Grid1")


if __name__ == "__main__":
    unittest.main()
