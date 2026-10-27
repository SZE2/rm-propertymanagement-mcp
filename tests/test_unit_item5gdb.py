#!/usr/bin/env python3
"""Offline unit tests for v2 items 5g (answer hints), 5d (collections recipe, sumif token), 5b (tenant_leases lookup)."""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server
from offline_fixture import setUpModule


def recipe(name):
    return next(r for r in server.load_recipes() if r["name"] == name)


class Hints(unittest.TestCase):
    def test_aged_and_delinquency_state_their_defaults(self):
        a, d = recipe("aged_receivables")["answer_hint"].lower(), recipe("delinquency")["answer_hint"].lower()
        self.assertIn("prospects not included", a); self.assertIn("no minimum", a)
        self.assertIn("prospects", d); self.assertIn("$100", d); self.assertIn("5 days", d)


class Sumif(unittest.TestCase):
    ROWS = [{"Description": "Rent Charge", "Collected": 100.0}, {"Description": "(Prepay) Rent Charge", "Collected": 5.5},
            {"Description": "Late Charge", "Collected": 7.0}]

    def test_sumif_contains_case_insensitive(self):
        self.assertEqual(server.eval_token("sumif", "Collected|Description|rent charge", self.ROWS, {}, {}), 105.5)

    def test_sumif_unknown_column(self):
        with self.assertRaises(server.ToolError):
            server.eval_token("sumif", "Collected|Nope|x", self.ROWS, {}, {})


class Collections(unittest.TestCase):
    GRID = {"Grid1": [], "Grid2": [
        {"ChargeTypesName": "RC", "ChargeTypesDescription": "Rent Charge", "Amount": 1000.0},
        {"ChargeTypesName": "+RC", "ChargeTypesDescription": "(Prepay) Rent Charge", "Amount": 50.0},
        {"ChargeTypesName": "LC", "ChargeTypesDescription": "Late Charge", "Amount": 25.0}]}

    def test_headline_and_params(self):
        seen = {}
        def fake(rep, p): seen.update(p); return self.GRID, "q"
        with mock.patch.object(server, "run_report_raw", fake), \
             mock.patch.object(server, "all_scope_label", lambda: "All test properties"), \
             mock.patch.object(server, "properties", lambda: []), \
             mock.patch.object(server, "save", lambda *a, **k: ("out/x.json", None)):
            out = server.run_recipe_result("collections", {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertTrue(out["text"].startswith("Answer: Collected $1,075.00 from 2026-09-01 to 2026-09-30 (rent $1,050.00)."), out["text"][:200])
        self.assertEqual(out["facts"]["total_collected"], 1075.0)
        self.assertEqual(seen["StartDate"], "2026-09-01")
        self.assertNotIn("Grid1", out["text"].split("\n")[0])


class TenantLeaseLookup(unittest.TestCase):
    def test_lookup_adds_term_and_mtm(self):
        leases = [{"LeaseID": 1, "TenantID": 7, "ActiveLeaseRenewal": {"LeaseTermID": 1}},
                  {"LeaseID": 2, "TenantID": 7, "ActiveLeaseRenewal": {"LeaseTermID": 3}},
                  {"LeaseID": 3, "TenantID": 7, "ActiveLeaseRenewal": {"LeaseTermID": -1}}]
        terms = [{"LeaseTermID": 1, "LeaseTerm": "MTM", "IsMonthToMonth": True}, {"LeaseTermID": 3, "LeaseTerm": "12 Months"}]
        def fake_api(path, params=None, timeout=None):
            self.assertEqual(path, "/LeaseTerms"); return 200, {}, terms
        with mock.patch.object(server, "fetch_ordered", lambda *a, **k: (leases, 3, 1, 200)), \
             mock.patch.object(server, "api", fake_api), \
             mock.patch.object(server, "save", lambda *a, **k: ("out/x.json", None)), \
             mock.patch.object(server, "resolve_tenant", lambda v, p=None: (7, "Tenant: T (ID 7)")), \
             mock.patch.object(server, "all_scope_label", lambda: "x"), \
             mock.patch.object(server, "properties", lambda: []):
            out = server.run_recipe_result("tenant_leases", {"tenant": "T"})
        rows = out["text"]
        self.assertIn("MTM|True", rows); self.assertIn("12 Months|False", rows)
        self.assertTrue(out["text"].startswith("Answer: 3 leases"))


if __name__ == "__main__":
    unittest.main()
