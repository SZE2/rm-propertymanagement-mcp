#!/usr/bin/env python3
"""Offline unit tests: occupancy from the physical-vacancy Units filters (post.mark/calc), conditional filters,
report_params pass-through, recipe knobs. No network, no credentials."""
import os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server
from offline_fixture import setUpModule

UNITS = [  # (UnitID, property, vacant)
    (1, "Oak", True), (2, "Oak", False), (3, "Oak", False), (4, "Oak", False),
    (5, "Elm", True), (6, "Elm", True), (7, "Elm", False),
]


def fake_fetch(path, prm, max_records=500, *a, **k):
    flt = prm.get("filters", "")
    rows = []
    for uid, prop, vac in UNITS:
        if "IsVacantAsOf" in flt and not vac: continue
        rows.append({"UnitID": uid, "PropertyID": 10 if prop == "Oak" else 11, "Property": {"Name": prop}})
    return rows, len(rows), 1, 200


def run(name, params=None, fetch=fake_fetch):
    seen = []
    def f(path, prm, *a, **k):
        seen.append(prm.get("filters", "")); return fetch(path, prm, *a, **k)
    with tempfile.TemporaryDirectory() as td, \
         mock.patch.dict(os.environ, {"RM_OUT": td}), \
         mock.patch.object(server, "find_resource", lambda n: {"model": "Unit", "path": "/Units"}), \
         mock.patch.object(server, "validate", lambda m, fl, em, flt, ob: (fl, em, flt, ("", ""), [])), \
         mock.patch.object(server, "fetch_ordered", f), \
         mock.patch.object(server, "resolve_property_ids", lambda v: ([], "")), \
         mock.patch.object(server, "all_scope_label", lambda: "All test properties"):
        res = server.run_recipe_result(name, params or {})
    return res, seen


class Occupancy(unittest.TestCase):
    def test_counts_and_percentages(self):
        res, seen = run("occupancy_summary", {"as_of": "2026-09-30"})
        f = res["facts"]
        self.assertEqual((f["units"], f["vacant"], f["occupied"]), (7, 3, 4))
        self.assertEqual(round(f["pct_vacant"] + f["pct_occupied"], 1), 100.0)
        self.assertEqual(f["pct_vacant"], 42.9)
        self.assertIn("3 of 7 units physically vacant (42.9%)", res["headline"])
        self.assertIn("Elm|11|3|2|1|66.70|33.30", res["text"])     # rows sorted by vacant desc

    def test_vacant_equals_vacant_units_recipe(self):
        occ, _ = run("occupancy_summary")
        vac, _ = run("vacant_units")
        self.assertEqual(occ["facts"]["vacant"], vac["facts"]["vacant"])

    def test_same_filters_as_vacant_units(self):
        _, occ = run("occupancy_summary", {"as_of": "2026-09-30"})
        _, vac = run("vacant_units", {"as_of": "2026-09-30"})
        self.assertEqual(occ[1], vac[0])                          # mark query == vacant_units filter
        self.assertNotIn("IsVacantAsOf", occ[0])                  # totals query = same minus the vacancy filter
        self.assertEqual(occ[0], vac[0].replace("IsVacantAsOf,eq,2026-09-30;", ""))

    def test_include_flags_drop_filters(self):
        _, seen = run("occupancy_summary", {"include_inactive_properties": True, "include_other_rentables": "yes"})
        self.assertNotIn("IsActive", seen[0]); self.assertNotIn("IsOtherRentableItem", seen[0])
        _, seen = run("vacant_units", {"include_inactive_properties": True})
        self.assertNotIn("IsActive", seen[0]); self.assertIn("IsOtherRentableItem", seen[0])

    def test_empty_units(self):
        res, _ = run("occupancy_summary", fetch=lambda *a, **k: ([], 0, 1, 204))
        self.assertEqual(res["facts"]["units"], 0)


class Helpers(unittest.TestCase):
    def test_apply_calc(self):
        row = {"count": 8, "vacant": 2.0}
        server.apply_calc(row, {"totalUnits": "count", "vacantUnits": "vacant", "occ": "count-vacant", "pv": "pct:vacant/count", "z": "pct:vacant/zero"})
        self.assertEqual((row["totalUnits"], row["vacantUnits"], row["occ"], row["pv"], row["z"]), (8, 2, 6, 25.0, None))

    def test_conditional_fill(self):
        self.assertEqual(server._fill("?!inc|A,eq,1", {"inc": "False"}), "A,eq,1")
        self.assertIsNone(server._fill("?!inc|A,eq,1", {"inc": "True"}))
        self.assertEqual(server._fill("?on|A,eq,{x}", {"on": "True", "x": 5}), "A,eq,5")
        self.assertIsNone(server._fill("?on|A,eq,1", {"on": "False"}))


class ReportParams(unittest.TestCase):
    def _run(self, params):
        captured = {}
        def fake_report(rep, rp, **kw):
            captured.update(rp); return "Scope: x\nbody"
        with mock.patch.object(server, "t_run_report", fake_report), \
             mock.patch.object(server, "resolve_property_ids", lambda v: ([], "")), \
             mock.patch.object(server, "all_scope_label", lambda: "All"):
            server.run_recipe_result("rent_roll", params)
        return captured

    def test_default_untouched_and_knob_sent(self):
        c = self._run({})
        self.assertEqual(c["ReportMethod"], "Current Tenants")
        self.assertEqual(self._run({"method": "Activity in Period"})["ReportMethod"], "Activity in Period")

    def test_report_params_passthrough(self):
        c = self._run({"report_params": "SHOWWHOLEDOLLAR,true;MRProration,Market Rent Based on 1 Month"})
        self.assertEqual(c["SHOWWHOLEDOLLAR"], "True")
        self.assertEqual(c["MRProration"], "Market Rent Based on 1 Month")

    def test_unknown_report_param_has_example(self):
        with self.assertRaises(server.ToolError) as e: self._run({"report_params": "Nope,1"})
        self.assertIn("e.g. parameters=", str(e.exception))

    def test_unknown_recipe_param_has_example(self):
        with self.assertRaises(server.ToolError) as e: self._run({"bogus": 1})
        self.assertIn("e.g. run_recipe(", str(e.exception))
        self.assertIn("report_params", str(e.exception))

    def test_flat_top_level_report_params_moved(self):
        a = server.flatten_recipe_args({"name": "rent_roll", "report_params": "SHOWWHOLEDOLLAR,true"})
        self.assertEqual(a["params"]["report_params"], "SHOWWHOLEDOLLAR,true")


class Knobs(unittest.TestCase):
    def test_defaults_unchanged(self):
        rs = {r["name"]: r for r in server.load_recipes()}
        d = rs["delinquency"]["params"]
        self.assertEqual((d["balance_over"]["default"], d["days_late_over"]["default"]), (100, 5))
        self.assertTrue(rs["delinquency"]["params"]["summary"]["default"])
        self.assertFalse(rs["aged_receivables"]["params"]["include_prospects"]["default"])
        self.assertEqual(rs["aged_receivables"]["params"]["balance_at_least"]["default"], "")
        self.assertEqual(rs["lease_expirations"]["params"]["tenants"]["default"], "Current")
        self.assertFalse(rs["profit_and_loss"]["params"]["show_hidden_rows"]["default"])

    def test_every_report_param_reference_exists_in_report(self):
        # every {placeholder} used in a report request names a declared recipe param
        for r in server.load_recipes():
            req = r["request"]
            if "report" not in req: continue
            for k, v in (req.get("parameters") or {}).items():
                for ph in __import__("re").findall(r"\{(\w+)\}", v):
                    self.assertIn(ph, r.get("params") or {}, "%s.%s" % (r["name"], k))


if __name__ == "__main__":
    unittest.main()
