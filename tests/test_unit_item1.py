#!/usr/bin/env python3
"""Offline unit tests for v2 item 1 (cut Haiku turns). No network, no credentials.
Run: python3 -m unittest discover -s tests -p "test_unit*.py"
"""
import os, sys, json, datetime, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server


class FixedToday:
    """Patch server._today to a fixed date (2026-10-13)."""
    def __enter__(self):
        self.p = mock.patch.object(server, "_today", lambda: datetime.date(2026, 10, 13)); self.p.start(); return self
    def __exit__(self, *a): self.p.stop()


class Catalog1a(unittest.TestCase):
    def test_every_recipe_in_run_recipe_description(self):
        desc = next(t for t in server.tool_list() if t["name"] == "run_recipe")["description"]
        for r in server.load_recipes():   # v3 4b: catalog:false recipes are hidden from the description
            if r.get("catalog") is False: self.assertNotIn(r["name"] + "(", desc); continue
            self.assertIn(r["name"] + "(", desc, r["name"])
        self.assertIn("find_recipe is optional", desc)

    def test_length_limits(self):
        for t in server.tool_list():
            self.assertLessEqual(len(t["description"]), 1900, t["name"])
        self.assertLessEqual(len(server.INSTRUCTIONS), 1500)   # 1d

    def test_catalog_shrinks_to_fit(self):
        # v3 4a: `use` text is never shortened, so only recipes without `use` shrink; whole catalog stays within the limit
        self.assertLessEqual(len(server.recipe_catalog(1500)), 1500)


class NearMiss1b(unittest.TestCase):
    def test_did_you_mean(self):
        with self.assertRaises(server.ToolError) as c:
            server.t_run_recipe("delinquent")
        self.assertIn("run_recipe(name='delinquency')", str(c.exception))
        self.assertIn("not run automatically", str(c.exception))

    def test_keyword_fallback(self):
        with self.assertRaises(server.ToolError) as c:
            server.t_run_recipe("noi")
        self.assertIn("Did you mean run_recipe(name='", str(c.exception))


class Annotations1c(unittest.TestCase):
    def test_annotations_title_prefix(self):
        for t in server.tool_list():
            a = t["annotations"]
            self.assertTrue(a["readOnlyHint"] and a["idempotentHint"] and "openWorldHint" in a, t["name"])
            self.assertTrue(t["title"], t["name"])
            self.assertTrue(t["description"].startswith("Rent Manager:"), t["name"])


class Instructions1d(unittest.TestCase):
    def test_names_every_tool_and_answer_rule(self):
        for t in server.tool_list():
            self.assertIn(t["name"], server.INSTRUCTIONS)
        self.assertIn("do no arithmetic", server.INSTRUCTIONS)


class Flatten1e(unittest.TestCase):
    def test_flat_equals_nested(self):
        flat = server.flatten_recipe_args({"name": "vacant_units", "property": "12", "as_of": "today"})
        nested = server.flatten_recipe_args({"name": "vacant_units", "params": {"property": "12", "as_of": "today"}})
        self.assertEqual(flat, nested)
        self.assertEqual(flat, {"name": "vacant_units", "params": {"property": "12", "as_of": "today"}})

    def test_call_tool_passes_flattened(self):
        seen = {}
        orig = server.TOOLS
        def fake(name, params=None, **kw): seen["p"] = params; return "ok"
        tools = [(n, d, p, r, fake if n == "run_recipe" else f) for n, d, p, r, f in orig]
        with mock.patch.object(server, "TOOLS", tools):
            server.call_tool("run_recipe", {"name": "vacant_units", "property": "12"})
        self.assertEqual(seen["p"], {"property": "12"})

    def test_unknown_key_still_rejected(self):
        with self.assertRaises(server.ToolError):
            server.call_tool("run_recipe", {"name": "vacant_units", "bogus": 1})


class Dates1f(unittest.TestCase):
    def test_parse(self):
        with FixedToday():
            P = server.parse_date
            self.assertEqual(P("2026-09"), "2026-09-01")
            self.assertEqual(P("2026-09", end=True), "2026-09-30")
            self.assertEqual(P("September 2026"), "2026-09-01")
            self.assertEqual(P("Sep 2026", end=True), "2026-09-30")
            self.assertEqual(P("sep"), "2026-09-01")
            self.assertEqual(P("december"), "2025-12-01")       # future month -> most recent
            self.assertEqual(P("last month"), "2026-09-01")
            self.assertEqual(P("last month", end=True), "2026-09-30")
            self.assertEqual(P("this month", end=True), "2026-10-31")
            self.assertEqual(P("ytd"), "2026-01-01")
            self.assertEqual(P("ytd", end=True), "2026-10-13")
            self.assertEqual(P("2026-02", end=True), "2026-02-28")
            self.assertEqual(P("2026-09-15"), "2026-09-15")
            with self.assertRaises(server.ToolError): P("nonsense")

    def test_period_range(self):
        with FixedToday():
            self.assertEqual(server.period_range("September 2026"), ("2026-09-01", "2026-09-30"))
            self.assertEqual(server.period_range("last_month"), ("2026-09-01", "2026-09-30"))
            with self.assertRaises(server.ToolError): server.period_range("2026-09-15")

    def test_pl_summary_period_sends_dates(self):
        seen = {}
        def fake_report(rid, rp, **kw): seen.update(rp); return "Scope: x\nrows"
        with mock.patch.object(server, "t_run_report", fake_report), \
             mock.patch.object(server, "all_scope_label", lambda: "all"), \
             mock.patch.object(server, "properties", lambda: []):
            server.t_run_recipe("profit_and_loss_summary", {"period": "September 2026"})
            self.assertEqual((seen["StartDate"], seen["EndDate"]), ("2026-09-01", "2026-09-30"))
            seen.clear()
            server.call_tool("run_recipe", {"name": "profit_and_loss_summary", "period": "2026-08"})
            self.assertEqual((seen["StartDate"], seen["EndDate"]), ("2026-08-01", "2026-08-31"))


class Hidden1h(unittest.TestCase):
    def test_hidden_params_not_in_schema_but_accepted(self):
        for t in server.tool_list():
            if t["name"] in ("query", "run_report"):
                props = t["inputSchema"]["properties"]
                for h in ("page_size", "parallel", "save_as"):
                    self.assertNotIn(h, props)
        # still callable: call_tool must not reject them (function receives them)
        seen = {}
        tools = [(n, d, p, r, (lambda **kw: seen.update(kw) or "ok") if n == "query" else f) for n, d, p, r, f in server.TOOLS]
        with mock.patch.object(server, "TOOLS", tools):
            server.call_tool("query", {"resource": "Units", "page_size": 50, "save_as": "x"})
        self.assertEqual(seen["page_size"], 50)


if __name__ == "__main__":
    unittest.main()
