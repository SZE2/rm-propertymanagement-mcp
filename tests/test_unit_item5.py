#!/usr/bin/env python3
"""Offline unit tests for v2 item 5e (">" thresholds) and 5a (basis)."""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server


def captured(recipe, params):
    """Run a recipe with the report call stubbed; return (report parameters sent, text)."""
    seen = {}
    def fake(rid, rp, **kw):
        seen.update(rp)
        return "Report X (id 1): Grid1 has 0 row(s). Scope: stub\n"
    with mock.patch.object(server, "t_run_report", fake), \
         mock.patch.object(server, "recipe_report", lambda req: 1), \
         mock.patch.object(server, "properties", lambda live=False: []), \
         mock.patch.object(server, "resolve_property_ids", lambda v: ([], "")):
        out = server.run_recipe_result(recipe, params)
    return seen, out["text"]


class Thresholds(unittest.TestCase):
    def test_defaults(self):
        rp, _ = captured("delinquency", {})
        self.assertEqual(float(rp["CUSTOMERVALUESTOINCLUDETEXT"]), 100.01)
        self.assertEqual(int(rp["DelinquentDaysToInclude"]), 6)

    def test_over_500(self):
        rp, _ = captured("delinquency", {"balance_over": 500, "days_late_over": 30})
        self.assertEqual(float(rp["CUSTOMERVALUESTOINCLUDETEXT"]), 500.01)
        self.assertEqual(int(rp["DelinquentDaysToInclude"]), 31)

    def test_old_names_alias(self):
        rp, _ = captured("delinquency_by_property", {"min_balance": 500, "min_days_late": 10})
        self.assertEqual(float(rp["CUSTOMERVALUESTOINCLUDETEXT"]), 500.01)
        self.assertEqual(int(rp["DelinquentDaysToInclude"]), 11)


class Basis(unittest.TestCase):
    def test_cash_accrual_default(self):
        for recipe in ("profit_and_loss", "profit_and_loss_summary"):
            rp, text = captured(recipe, {"basis": "Accrual"})
            self.assertEqual(str(rp["Basis"]), "1"); self.assertIn("Basis: Accrual", text)
            rp, text = captured(recipe, {"basis": "cash"})
            self.assertEqual(str(rp["Basis"]), "2"); self.assertIn("Basis: Cash", text)
            rp, text = captured(recipe, {})
            self.assertNotIn("Basis", rp); self.assertIn("Basis: Cash (Rent Manager default)", text)

    def test_bad_basis(self):
        with self.assertRaises(server.ToolError):
            captured("profit_and_loss", {"basis": "weird"})


if __name__ == "__main__":
    unittest.main()
