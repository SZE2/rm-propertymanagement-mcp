#!/usr/bin/env python3
"""Offline unit tests for v2 item 2 (Answer headline, facts, NOI, bundles, delinquency output). No network, no credentials.
Run: python3 -m unittest discover -s tests -p "test_unit*.py"
"""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server
from offline_fixture import setUpModule

ROWS = [
    {"Property": "Oak", "Tenant": "Ann", "Unit": "002", "Owed": 1500.5, "vacantUnits": 3, "totalUnits": 12, "0_30": 10.0},
    {"Property": "Elm", "Tenant": "Bob", "Unit": "7", "Owed": 400.25, "vacantUnits": 1, "totalUnits": 8, "0_30": 5.0},
    {"Property": "Oak", "Tenant": "Cy", "Unit": "9", "Owed": -50.0, "vacantUnits": 0, "totalUnits": 0, "0_30": 0.0},
]


def R(t, rows=ROWS, vals=None, facts=None):
    return server.render_headline(t, rows, vals or {}, facts or {})


class Tokens(unittest.TestCase):
    def test_count(self):
        self.assertEqual(R("{count} rows"), "3 rows")
        self.assertEqual(R("{count} rows", []), "0 rows")

    def test_sum_plain_and_money(self):
        self.assertEqual(R("{sum:Owed}"), "1,850.75")
        self.assertEqual(R("{sum$:Owed}"), "$1,850.75")
        self.assertEqual(R("{sum:vacantUnits}"), "4")           # integral sums print without decimals
        self.assertEqual(R("{sum$:0_30}"), "$15.00")             # column names may start with a digit
        self.assertEqual(R("{sum$:Owed}", [{"Owed": -5}]), "-$5.00")
        self.assertEqual(R("{sum$:Owed}", []), "$0.00")

    def test_top1_and_last(self):
        self.assertEqual(R("{top1:Tenant}"), "Ann")
        self.assertEqual(R("{top1$:Owed}"), "$1,500.50")
        self.assertEqual(R("{last:Tenant}"), "Cy")
        self.assertEqual(R("{top1:Unit}"), "002")                # strings that look numeric stay as typed
        self.assertEqual(R("{top1:Tenant}", []), "n/a")

    def test_pct(self):
        self.assertEqual(R("{pct:vacantUnits/totalUnits}"), "20.0%")
        self.assertEqual(R("{pct:vacantUnits/totalUnits}", [{"vacantUnits": 1, "totalUnits": 0}]), "n/a")

    def test_facts_and_vals(self):
        self.assertEqual(R("{noi$} on {as_of}", vals={"as_of": "2026-09-30"}, facts={"noi": 1234567.89}), "$1,234,567.89 on 2026-09-30")

    def test_unknown_column_and_token(self):
        with self.assertRaises(server.ToolError): R("{sum:Nope}")
        with self.assertRaises(server.ToolError): R("{bogus}")


class Facts(unittest.TestCase):
    PL = [{"GLAccountTypeName": "Income", "count": 42, "Amount": 1000.0},
          {"GLAccountTypeName": "Expense", "count": 85, "Amount": 400.5},
          {"GLAccountTypeName": "Non Operating Expense", "count": 4, "Amount": 99.0}]
    RECIPE = {"post": {"sum": "Amount", "noi": {"label": "GLAccountTypeName", "value": "Amount", "income": "Income", "expense": "Expense"}},
              "facts": {"total": "sum:Amount"}}

    def test_noi_from_income_minus_expense_only(self):
        f = server.compute_facts(self.RECIPE, self.PL, {"start": "2026-09-01"}, "scope x")
        self.assertEqual((f["income"], f["expense"], f["noi"]), (1000.0, 400.5, 599.5))
        self.assertEqual(f["total"], 1499.5)             # alias evaluated from the same rows
        self.assertEqual((f["count"], f["start"], f["scope"]), (3, "2026-09-01", "scope x"))
        self.assertEqual(R("NOI {noi$}", self.PL, facts=f), "NOI $599.50")

    def test_every_shipped_headline_renders(self):
        """Each recipe headline uses only known tokens; columns it references are filled from the template itself."""
        vals = {"as_of": "2026-01-01", "start": "2026-01-01", "end": "2026-01-31"}
        for r in server.load_recipes():
            h = r.get("headline")
            if not h: continue
            cols = set()
            for tok in (r.get("facts") or {}).values():
                if tok.startswith("sumif:"): cols.update(x.strip() for x in tok[6:].split("|")[:2])
            for kind, _, arg in server._TOKEN.findall(h):
                if kind in ("sum", "top1", "last", "newest"): cols.add(arg)
                if kind == "pct": cols.update(x.strip() for x in arg.split("/"))
                if kind == "sumif": cols.update(x.strip() for x in arg.split("|")[:2])
            row = {c: 1 for c in cols}
            row.update({"GLAccountTypeName": "Income", "Amount": 5})
            facts = server.compute_facts(r, [row], vals)
            out = server.render_headline(h, [row], vals, facts)
            self.assertNotIn("{", out, r["name"])
            if r["name"].startswith("delinquency"):
                self.assertNotIn("ercent", out)


class RecipeOutput(unittest.TestCase):
    def run_recipe(self, name, grids_json, params=None):
        with mock.patch.object(server, "run_report_raw", lambda rep, p: (grids_json, "q")), \
             mock.patch.object(server, "all_scope_label", lambda: "All test properties"), \
             mock.patch.object(server, "properties", lambda live=False: []), \
             mock.patch.object(server, "save", lambda *a, **k: ("out/x.json", None)):
            return server.run_recipe_result(name, params or {})

    def test_pl_summary_headline_first_line_and_no_total_row(self):
        g = {"Grid1": [
            {"GLAccountTypeName": "Income", "Amount": 1000.0, "HideRow": False},
            {"GLAccountTypeName": "Income", "Amount": 250.0, "HideRow": False},
            {"GLAccountTypeName": "Expense", "Amount": 400.0, "HideRow": False},
            {"GLAccountTypeName": "Other Income", "Amount": 9999.0, "HideRow": False},
            {"GLAccountTypeName": "Expense", "Amount": 7.0, "HideRow": True}]}
        res = self.run_recipe("profit_and_loss_summary", g, {"period": "2026-09"})
        first = res["text"].split("\n")[0]
        self.assertTrue(first.startswith("Answer: NOI $850.00 for 2026-09-01 to 2026-09-30"), first)
        self.assertNotIn("TOTAL", res["text"])
        self.assertEqual(res["facts"]["noi"], 850.0)

    def test_delinquency_no_percent_no_small_grids(self):
        g = {"Grid1": [{"EntitiesName": "Oak", "AccountsName": "Ann", "AccountsAccountID": 1, "SubEntitiesName": "1", "isprospect": False, "ChargesAmount": 300.0},
                       {"EntitiesName": "Oak", "AccountsName": "Ann", "AccountsAccountID": 1, "SubEntitiesName": "1", "isprospect": False, "ChargesAmount": 200.0},
                       {"EntitiesName": "Elm", "AccountsName": "Bob", "AccountsAccountID": 2, "SubEntitiesName": "2", "isprospect": True, "ChargesAmount": 150.0}],
             "Grid2": [{"NumberOfCustomers": 99, "ReportSummaryDetail": "Percent Delinquent:", "ReportSummaryValue": "1.63%"}]}
        for name in ("delinquency", "delinquency_by_property"):
            res = self.run_recipe(name, g)
            self.assertNotIn("ercent", res["text"], name)
            self.assertTrue(res["text"].startswith("Answer: "), name)
        res = self.run_recipe("delinquency", g)
        self.assertIn("2 delinquent accounts", res["headline"])
        self.assertIn("owe $650.00", res["headline"])
        self.assertIn("largest: Ann ($500.00)", res["headline"])
        self.assertEqual(res["facts"]["total_owed"], 650.0)
        self.assertEqual(res["facts"]["accounts"], 2)

    def test_other_recipes_keep_small_grids(self):
        g = {"Grid1": [{"EntitiesShortName": "Oak", "AccountsName": "A", "SubEntitiesName": "1", "LessThan30": 1, "LessThan60": 0, "LessThan90": 0, "GreaterThan90": 0, "TotalLeft": 1}],
             "Grid2": [{"x": 1}]}
        res = self.run_recipe("aged_receivables", g)
        self.assertIn("Grid2:", res["text"])
        self.assertTrue(res["text"].startswith("Answer: Total receivable $1.00 across 1 accounts"), res["text"][:120])


class Bundles(unittest.TestCase):
    def test_bundle_prints_headlines_failed_and_scope(self):
        def fake(name, params=None, all_properties=False, show=15):
            if name == "move_ins": raise server.ToolError("boom")
            return {"text": "Answer: H-%s\nRecipe %s.\nbody" % (name, name), "headline": "H-" + name, "facts": {"n": 1}, "scope": "s"}
        with mock.patch.object(server, "run_recipe_result", fake), \
             mock.patch.object(server, "all_scope_label", lambda: "All test properties"), \
             mock.patch.object(server, "properties", lambda live=False: []):
            snap = next(x for x in server.load_recipes() if x["name"] == "property_snapshot")
            out = server.run_bundle(snap, {"property": "", "_scope": "Property: Oak (ID 1)"}, 15, 0)
        t = out["text"]
        self.assertIn("Scope: Property: Oak (ID 1)", t)
        self.assertIn("Answer [Occupancy]: H-occupancy_summary", t)
        self.assertIn("FAILED part [Move-ins this month]: boom", t)
        self.assertEqual(t.count("Answer: H-"), 0)           # per-part Answer lines are folded into the summary lines
        self.assertEqual(out["facts"]["failed"], ["Move-ins this month"])
        self.assertIn("1 FAILED", t)


class Wrapper(unittest.TestCase):
    def test_t_run_recipe_returns_text_of_result(self):
        with mock.patch.object(server, "run_recipe_result", lambda *a, **k: {"text": "T"}):
            self.assertEqual(server.t_run_recipe("x"), "T")

    def test_limits_still_hold(self):
        for t in server.tool_list():
            self.assertLessEqual(len(t["description"]), 1900, t["name"])
        self.assertLessEqual(len(server.INSTRUCTIONS), 1500)


if __name__ == "__main__":
    unittest.main()
