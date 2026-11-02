#!/usr/bin/env python3
"""Offline unit tests (v13): result delivery. Inline tables fit the reply cap in whole rows; a MORE line offers read_result (any client,
through MCP) and the local File; read_result pages a saved result completely; clip keeps the MORE/File lines; RM_DELIVERY modes.
Synthetic data only; no network, no credentials."""
import os, sys, csv, json, re, io, shutil, subprocess, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server

ROWS = [{"LeaseID": i, "UnitID": 1000 + i, "MoveInDate": "2020-01-%02d" % (i % 28 + 1), "Note": "a, \"quoted\" note" if i % 7 == 0 else ""}
        for i in range(1941)]


def pages(rid, **kw):
    """Collect every row through read_result exactly as a client without file access would."""
    got, off, calls = [], 0, 0
    while True:
        text = server.t_read_result(rid, offset=off, **kw)
        calls += 1
        lines = text.splitlines()
        end = [l for l in lines if l.startswith("END OF PAGE")][0]
        got += list(csv.DictReader(io.StringIO("\n".join(lines[1:lines.index(end)]))))
        m = re.search(r"offset=(\d+)", end)
        if not m: return got, calls, text
        off = int(m.group(1))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"RM_OUT": self.tmp}); self.env.start()
        os.environ.pop("RM_DELIVERY", None)
    def tearDown(self):
        self.env.stop(); shutil.rmtree(self.tmp, ignore_errors=True)


class FitTable(Base):
    def test_whole_rows_within_budget(self):
        text, n = server.fit_table(ROWS, 2500, None, 5000)
        self.assertLessEqual(len(text), 5000)
        self.assertTrue(0 < n < len(ROWS))
        self.assertEqual(len(text.splitlines()), n + 1)          # header + n complete rows, nothing cut mid-row
    def test_small_result_unchanged(self):
        text, n = server.fit_table(ROWS[:5], 40)
        self.assertEqual((text, n), (server.table(ROWS[:5], 40), 5))
    def test_empty(self):
        self.assertEqual(server.fit_table([], 40), ("(no rows)", 0))


class Deliver(Base):
    def test_all_shown_lists_files_without_more(self):
        jp, cp = server.save("Leases", ROWS[:3], ROWS[:3])
        out = server.deliver(ROWS[:3], 3, jp, cp, ROWS[:3])
        self.assertNotIn("MORE", out); self.assertIn("File: " + os.path.abspath(cp), out)
        self.assertNotIn(server.FILE_HINT, out)                  # data results: no 'show the file to the user' boilerplate
    def test_more_offers_both_routes_and_pages_completely(self):
        jp, cp = server.save("Leases", ROWS, ROWS)
        out = server.deliver(ROWS, 40, jp, cp, ROWS)
        self.assertTrue(out.startswith("MORE: 40 of 1941 rows shown; the rest is NOT in this reply."))
        rid = re.search(r"read_result\(result_id='([^']+)', offset=40\)", out).group(1)
        self.assertEqual(rid, server.result_id(cp))
        self.assertIn("File: " + os.path.abspath(cp), out)
        got, calls, last = pages(rid)
        self.assertEqual([int(r["LeaseID"]) for r in got], [r["LeaseID"] for r in ROWS])
        self.assertEqual(got[7]["Note"], 'a, "quoted" note')     # CSV quoting round-trips
        self.assertEqual(got[0]["MoveInDate"], "2020-01-01")
        self.assertGreater(calls, 1)
        self.assertIn("All 1941 rows delivered.", last)
    def test_view_saved_when_table_differs_from_saved_rows(self):
        jp, cp = server.save("Leases", ROWS, ROWS)
        view = [{"UnitID": r["UnitID"]} for r in reversed(ROWS)]
        out = server.deliver(view, 10, jp, cp, ROWS)
        rid = re.search(r"result_id='([^']+)'", out).group(1)
        self.assertIn("_view_", rid)
        self.assertTrue(server._STAMPED.search(rid + ".csv"))    # pruned like every other result file
        got, _, _ = pages(rid)
        self.assertEqual([int(r["UnitID"]) for r in got], [r["UnitID"] for r in view])
    def test_modes(self):
        jp, cp = server.save("Leases", ROWS, ROWS)
        with mock.patch.dict(os.environ, {"RM_DELIVERY": "mcp"}):
            out = server.deliver(ROWS, 40, jp, cp, ROWS)
            self.assertIn("pages it (any client)", out); self.assertNotIn("File:", out); self.assertNotIn(self.tmp, out)
            self.assertEqual(server.deliver(ROWS[:1], 1, jp, cp, ROWS[:1]), "")
        with mock.patch.dict(os.environ, {"RM_DELIVERY": "file"}):
            out = server.deliver(ROWS, 40, jp, cp, ROWS)
            self.assertIn("File: " + os.path.abspath(cp), out); self.assertNotIn("read_result", out)


class ReadResult(Base):
    def setUp(self):
        super().setUp()
        self.jp, self.cp = server.save("Leases", ROWS, ROWS)
        self.rid = server.result_id(self.cp)
    def test_columns_limit_and_page_size(self):
        t = server.t_read_result(self.rid, offset=100, limit=5, columns="LeaseID")
        lines = t.splitlines()
        self.assertEqual(lines[1], "LeaseID"); self.assertEqual(lines[2:7], [str(i) for i in range(100, 105)])
        self.assertIn("rows 101-105 of 1941", t); self.assertIn("offset=105, columns='LeaseID')", t)
        big = server.t_read_result(self.rid, max_chars=10 ** 9)
        self.assertLessEqual(len(big), server.PAGE_CHARS_MAX + 500)
    def test_accepts_path_or_filename(self):
        self.assertIn("rows 1-", server.t_read_result(self.cp, limit=1))
        self.assertIn("rows 1-", server.t_read_result(os.path.basename(self.cp), limit=1))
    def test_midnight_timestamps_become_dates(self):
        jp, cp = server.save("T", [], [{"D": "2024-05-01T00:00:00", "E": "2024-05-01T13:45:00"}])
        t = server.t_read_result(server.result_id(cp))
        self.assertIn("\n2024-05-01,2024-05-01T13:45:00\n", t)
    def test_json_only_result(self):
        jp, cp = server.save("Raw", [{"A": {"B": 1}}, {"A": {"B": 2}}], None)
        t = server.t_read_result(server.result_id(jp))
        self.assertIn("A.B", t); self.assertIn("All 2 rows delivered.", t)
    def test_errors(self):
        for bad in ("../secret", "..", "", "a/b/../../etc"):
            with self.assertRaises(server.ToolError): server.t_read_result(bad)
        with self.assertRaises(server.ToolError): server.t_read_result("Nope_20260101-000000")
        with self.assertRaises(server.ToolError): server.t_read_result(self.rid, offset=5000)
        with self.assertRaises(server.ToolError): server.t_read_result(self.rid, columns="NoSuchCol")


class Clip(unittest.TestCase):
    def test_keeps_more_lines_and_cuts_at_line(self):
        body = "\n".join("row %d|xxxxxxxxxxxxxxxxxxxxxxxx" % i for i in range(1000))
        tail = "\nSee MORE above: read_result(result_id='X_20260101-000000', offset=40)\n(1.0s, live)"
        out = server.clip("head\n" + body + tail)
        self.assertLessEqual(len(out), server.MAX_TEXT)
        self.assertTrue(out.endswith(tail))
        self.assertRegex(out, r"\n\.\.\.\[\d+ characters of this reply cut here\]\nSee MORE above: ")
        cut_line = out.split("\n...[")[0].splitlines()[-1]
        self.assertTrue(cut_line.endswith("x"))                 # last kept line is whole


BIG = [{"AccountID": i, "Name": "Tenant %d" % i, "Owed": 100 + i % 50} for i in range(1941)]
NOTICE = re.compile(r"^MORE: (\d+) of (\d+) rows shown")


class TopNotice(Base):
    """v14: the 'rows are missing' notice is the FIRST line (second after CAPPED/PARTIAL), the bottom is one short pointer."""
    def reply(self, rows=ROWS, show=0, **kw):
        jp, cp = server.save("Leases", rows, rows)
        lim, large = server.show_plan(show, len(rows))
        body, n = server.fit_table(rows, lim, None, server.MAX_TEXT - server.INLINE_RESERVE - 200)
        text = "Leases: head line. Scope: all test properties\n%s\n%s\n(0.1s, live)" % (body, server.deliver(rows, n, jp, cp, rows, large))
        return server.clip(text), n, cp

    def test_notice_first_and_bottom_is_one_short_line(self):
        out, n, cp = self.reply()
        lines = out.split("\n")
        self.assertRegex(lines[0], NOTICE)
        self.assertEqual(lines[0].count("MORE:"), 1)
        self.assertIn("read_result(result_id='%s', offset=%d) pages it (any client)" % (server.result_id(cp), n), lines[0])
        self.assertIn("File: " + os.path.abspath(cp) + " if your file tools run on this computer", lines[0])
        self.assertIn("narrow the request (date range, property) or use a built-in report.", lines[0])
        self.assertEqual(sum(1 for l in lines if l.startswith("MORE:")), 1)
        self.assertEqual(lines[-1], "(0.1s, live)")
        self.assertTrue(lines[-2].startswith("See MORE above: read_result("))
        self.assertLess(len(lines[-2]), 120)
        self.assertNotIn("pick one", out)
        self.assertNotIn("File: ", "\n".join(lines[-2:]))          # the path appears once, in the notice

    def test_second_after_capped_and_before_answer(self):
        out, n, cp = self.reply()
        notice, body = out.split("\n", 1)[0], out.split("\n", 1)[1]
        raw = "CAPPED: showing the newest 5000 of 9000 rows. More exist.\nAnswer: 1941 accounts.\nRecipe x. hint\n" + body + "\n" + notice
        lifted = server.lift_notice(raw)
        lines = lifted.split("\n")
        self.assertTrue(lines[0].startswith("CAPPED:"))
        self.assertRegex(lines[1], NOTICE)
        self.assertTrue(lines[2].startswith("Answer:"))
        self.assertEqual(server.lift_notice(lifted), lifted)       # idempotent
        self.assertEqual(server.lift_notice("PARTIAL: x\nhead\nMORE: 3 of 9 rows shown; z\nrow").split("\n")[1][:5], "MORE:")
        self.assertEqual(server.lift_notice("no notice here\nMORE: not a notice"), "no notice here\nMORE: not a notice")

    def test_no_notice_when_all_rows_shown(self):
        jp, cp = server.save("Leases", ROWS[:3], ROWS[:3])
        out = server.clip("head\n%s\n%s" % (server.table(ROWS[:3], 40), server.deliver(ROWS[:3], 3, jp, cp, ROWS[:3])))
        self.assertNotIn("MORE", out); self.assertNotIn("See MORE", out)
        self.assertTrue(out.splitlines()[-1].startswith("File: "))

    def test_modes_top_line(self):
        jp, cp = server.save("Leases", ROWS, ROWS)
        with mock.patch.dict(os.environ, {"RM_DELIVERY": "mcp"}):
            out = server.clip("head\n" + server.deliver(ROWS, 40, jp, cp, ROWS))
            first = out.split("\n")[0]
            self.assertRegex(first, NOTICE); self.assertIn("read_result(result_id=", first)
            self.assertNotIn("File:", out); self.assertNotIn(self.tmp, out)
        with mock.patch.dict(os.environ, {"RM_DELIVERY": "file"}):
            out = server.clip("head\n" + server.deliver(ROWS, 40, jp, cp, ROWS))
            first = out.split("\n")[0]
            self.assertRegex(first, NOTICE); self.assertIn("File: " + os.path.abspath(cp), first)
            self.assertNotIn("read_result", out)
        with mock.patch.dict(os.environ, {"RM_DELIVERY": "auto"}):
            first = server.clip("head\n" + server.deliver(ROWS, 40, jp, cp, ROWS)).split("\n")[0]
            self.assertIn("read_result(", first); self.assertIn("File: ", first)

    def test_sample_above_big_rows_and_explicit_show_wins(self):
        self.assertEqual(server.show_plan(0, 1000), (server.SHOW_ROWS, False))          # at the threshold: normal
        self.assertEqual(server.show_plan(0, 1001), (server.SAMPLE_ROWS, True))
        self.assertEqual(server.show_plan(None, 1941), (10, True))
        self.assertEqual(server.show_plan(25, 1941), (25, False))                        # explicit show honoured
        with mock.patch.dict(os.environ, {"RM_BIG_ROWS": "50", "RM_SAMPLE_ROWS": "3"}):
            self.assertEqual(server.show_plan(0, 51), (3, True)); self.assertEqual(server.show_plan(0, 50), (40, False))
        with mock.patch.dict(os.environ, {"RM_BIG_ROWS": "junk", "RM_SAMPLE_ROWS": "also junk"}):
            self.assertEqual(server.show_plan(0, 5000), (10, True)); self.assertEqual(server.show_plan(0, 1000), (40, False))
        out, n, cp = self.reply(BIG)
        self.assertEqual(n, 10)
        first = out.split("\n")[0]
        self.assertRegex(first, NOTICE)
        self.assertIn("(LARGE RESULT: 1941 rows)", first)
        self.assertIn("MORE: 10 of 1941 rows shown", first)
        self.assertEqual(len(out.split("\n")[2:][0].split("|")), 3)                      # header row of the sample table
        out2, n2, _ = self.reply(BIG, show=25)
        self.assertEqual(n2, 25); self.assertNotIn("LARGE RESULT", out2.split("\n")[0])
        self.assertIn("MORE: 25 of 1941 rows shown", out2.split("\n")[0])
        out3, n3, _ = self.reply(ROWS[:500])                                             # under the threshold: usual default
        self.assertEqual(n3, 40)

    def test_explicit_show_is_still_size_fitted(self):
        wide = [{"A": "x" * 400, "B": i} for i in range(300)]
        out, n, cp = self.reply(wide, show=300)
        self.assertLess(n, 300)
        self.assertLessEqual(len(out), server.MAX_TEXT)
        self.assertRegex(out.split("\n")[0], NOTICE)

    def test_clip_keeps_top_lines_with_long_notice_and_path(self):
        long_dir = os.path.join(self.tmp, "d" * 150)
        os.makedirs(long_dir)
        with mock.patch.dict(os.environ, {"RM_OUT": long_dir}):
            jp, cp = server.save("Leases", ROWS, ROWS)
            notice = server.deliver(ROWS, 40, jp, cp, ROWS)
        self.assertGreater(len(notice.split("\n")[0]), 380)
        # worst case: fit_table fills its whole budget, plus every other line a reply carries
        text = "CAPPED: showing the newest 5000 of 9000 rows. More exist.\nAnswer: x\nhead\n"
        body = "\n".join("r%d|%s" % (i, "y" * 60) for i in range(400))
        out = server.clip(text + body + "\n" + notice + "\n(1.0s, 5 page(s), live)\nTip: pass fields='A,B,C' to limit columns")
        lines = out.split("\n")
        self.assertLessEqual(len(out), server.MAX_TEXT)
        self.assertTrue(lines[0].startswith("CAPPED:")); self.assertRegex(lines[1], NOTICE)
        self.assertIn("characters of this reply cut here", out)
        self.assertIn("See MORE above", out.split("cut here]")[1])
        # the budget reserved for the notice, pointer and timing really covers the longest notice + pointer
        self.assertLess(len(notice) + 80, server.INLINE_RESERVE)

    def test_clip_never_cuts_top_even_when_nothing_else_fits(self):
        top = "MORE: 5 of 9 rows shown; " + "z" * 200
        out = server.clip(top + "\n" + "q" * (server.MAX_TEXT * 2))
        self.assertTrue(out.startswith(top))

    def test_end_to_end_recipe_reply(self):
        """run_recipe_result over a synthetic 1941-account delinquency grid: CAPPED/Answer ordering, 10-row sample, one short bottom line."""
        grid = {"Grid1": [{"EntitiesName": "Oak", "AccountsName": "T%04d" % i, "AccountsAccountID": i, "SubEntitiesName": str(i),
                           "isprospect": False, "ChargesAmount": 300.0 + i} for i in range(1941)]}
        with mock.patch.object(server, "run_report_raw", lambda rep, p: (grid, "q")), \
             mock.patch.object(server, "all_scope_label", lambda: "All test properties"), \
             mock.patch.object(server, "properties", lambda live=False: []):
            res = server.run_recipe_result("delinquency", {})
        lines = res["text"].split("\n")
        self.assertRegex(lines[0], NOTICE)
        self.assertIn("LARGE RESULT: 1941 rows", lines[0])
        self.assertTrue(lines[1].startswith("Answer: "))
        self.assertEqual(sum(1 for l in lines if l.startswith("MORE:")), 1)
        rid = re.search(r"read_result\(result_id='([^']+)', offset=10\)", lines[0]).group(1)
        self.assertIn("See MORE above: read_result(result_id='%s', offset=10)" % rid, res["text"])
        got, _, _ = pages(rid)
        self.assertEqual(len(got), 1941)
        # the same through the tool dispatcher (explicit show)
        with mock.patch.object(server, "run_report_raw", lambda rep, p: (grid, "q")), \
             mock.patch.object(server, "all_scope_label", lambda: "All test properties"), \
             mock.patch.object(server, "properties", lambda live=False: []):
            text = server.call_tool("run_recipe", {"name": "delinquency", "show": 20})
        self.assertRegex(text.split("\n")[0], NOTICE); self.assertIn("MORE: 20 of 1941", text.split("\n")[0])


class ToolSchema(unittest.TestCase):
    def test_listed_read_only_no_location(self):
        t = [x for x in server.tool_list() if x["name"] == "read_result"][0]
        self.assertTrue(t["annotations"]["readOnlyHint"]); self.assertFalse(t["annotations"]["openWorldHint"])
        self.assertNotIn("location", t["inputSchema"]["properties"])
        self.assertIn("read_result", server.INSTRUCTIONS); self.assertIn("MORE:", server.INSTRUCTIONS)


class Stdio(Base):
    def test_read_result_over_real_stdio(self):
        """The MCP path a remote client uses: no file access, only tools/call."""
        jp, cp = server.save("Leases", ROWS, ROWS)
        env = dict(os.environ, RM_OUT=self.tmp, RM_MCP_LOG="off")
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], cwd=HERE, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            def rpc(i, method, params):
                p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n").encode()); p.stdin.flush()
                while True:
                    r = json.loads(p.stdout.readline())
                    if r.get("id") == i: return r
            rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
            self.assertIn("read_result", [t["name"] for t in rpc(2, "tools/list", {})["result"]["tools"]])
            r = rpc(3, "tools/call", {"name": "read_result", "arguments": {"result_id": server.result_id(cp), "offset": 1900}})["result"]
            self.assertFalse(r["isError"])
            self.assertIn("rows 1901-1941 of 1941", r["content"][0]["text"]); self.assertIn("All 1941 rows delivered.", r["content"][0]["text"])
        finally:
            p.stdin.close(); p.wait(10)


if __name__ == "__main__":
    unittest.main()
