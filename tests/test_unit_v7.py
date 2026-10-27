#!/usr/bin/env python3
"""Offline unit tests (v7, Haiku feedback #1): concept pointers, in() normalisation, newest-first / first_per, rent summary
(tenant + subsidy portions), ledger summary, charge-type families, vacancy `also` rows + reconciliation. Generic data only."""
import os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server
from offline_fixture import setUpModule


class InFilter(unittest.TestCase):
    def test_forms(self):
        n = server.normalize_in
        self.assertEqual(n("UnitID,in,1;2;3"), "UnitID,in,(1,2,3)")
        self.assertEqual(n("UnitID,in,1,2,3"), "UnitID,in,(1,2,3)")
        self.assertEqual(n("UnitID,in,(1;2;3)"), "UnitID,in,(1,2,3)")
        self.assertEqual(n("UnitID,in,(1, 2)"), "UnitID,in,(1,2)")
        self.assertEqual(n("UnitID,in,(1,2)"), "UnitID,in,(1,2)")

    def test_clauses_survive(self):
        f = server.normalize_in("PropertyID,eq,5;UnitID,in,7;8;9;Name,eq,x")
        self.assertEqual(server.split_top(f), ["PropertyID,eq,5", "UnitID,in,(7,8,9)", "Name,eq,x"])
        self.assertEqual(server.split_top(server.normalize_in("Name,in,(a;b);UnitID,eq,1")), ["Name,in,(a,b)", "UnitID,eq,1"])

    def test_validate_batches(self):
        self.assertEqual(server.validate("Unit", "", "", "UnitID,in,5;6;7")[2], "UnitID,in,(5,6,7)")

    def test_bad_filter_has_alias_hint_and_closest(self):
        probs = server.validate("Unit", "", "", "UnitNumber,eq,5")[4]
        self.assertTrue(probs and "Use: Name" in probs[0] and "Close filterable fields" in probs[0])


class Concepts(unittest.TestCase):
    def test_rent_pointer(self):
        txt = "\n".join(server.concept_lines("last rent for vacant units"))
        self.assertIn("RECURRING CHARGES", txt); self.assertIn("subsidy", txt.lower())
        self.assertIn("vacancy", txt)
        self.assertIn("Leases", "\n".join(server.model_notes(["Leases"])))

    def test_words(self):
        for q, cid in [("security deposit", "deposit"), ("work orders", "work_order"), ("move-out date", "move_out"),
                       ("tenant balance", "balance"), ("section 8", "rent"), ("last tenant", "last_tenant")]:
            self.assertIn(cid, [c["id"] for c in server.concept_hits(q, 5)], q)

    def test_find_recipe_shows_pointer(self):
        self.assertIn("Where is the data (rent)", server.t_find_recipe("what is the asking rent"))

    def test_budgets(self):
        self.assertLessEqual(len(server.INSTRUCTIONS), 1500)
        for t in server.tool_list(): self.assertLessEqual(len(t["description"]), 1900, t["name"])
        self.assertIn("in,(1,2,3)", next(t for t in server.tool_list() if t["name"] == "query")["description"])


class ChargeFamilies(unittest.TestCase):
    def test_defaults(self):
        c = server.classify_charge_type
        self.assertEqual(c("RC", "Rent Charge"), "rent")
        self.assertEqual(c("U&O", "Use and Occupancy"), "rent")
        for code, desc in [("RA", "Rental Assistance"), ("HAP", "HAP"), ("X", "Section 8 voucher"), ("MS8", "MS8")]:
            self.assertEqual(c(code, desc), "subsidy", code)
        for code, desc in [("PETFEE", "Pet Rent"), ("RAL", "Rental Assistance Loss"), ("CON", "Concession"), ("LC", "Late Charge"), ("S", "Shape")]:
            self.assertIsNone(c(code, desc), code)

    def test_local_file_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "ct.json")
            open(fp, "w").write('{"rent": ["ZZ"], "subsidy": ["QQ"]}')
            with mock.patch.object(server, "CHARGE_TYPES_FILE", fp):
                self.assertEqual(server.charge_family("zz", "x"), "rent")
                self.assertEqual(server.charge_family("QQ", "x"), "subsidy")
                self.assertIsNone(server.charge_family("RC", "Rent Charge"))   # file is the authority once it exists


def RC(tid, uid, amt, frm, to, code, desc):
    return {"EntityKeyID": tid, "UnitID": uid, "Amount": amt, "FromDate": frm + "T00:00:00", "ToDate": (to + "T00:00:00") if to else None,
            "ChargeType.Name": code, "ChargeType.Description": desc}


class RentSummary(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(server, "CHARGE_TYPES_FILE", os.path.join(tempfile.gettempdir(), "no_such_charge_types.json"))
        p.start(); self.addCleanup(p.stop)

    def test_tenant_plus_subsidy_is_total(self):
        rows = [RC(1, 10, 727, "2019-03-01", "", "RC", "Rent Charge"), RC(1, 10, 158, "2019-03-01", "", "RA", "Rental Assistance"),
                RC(1, 10, 25, "2019-03-01", "", "PETFEE", "Pet Rent")]
        out = server.summarize_rent(rows)
        self.assertEqual(len(out), 1)
        o = out[0]
        self.assertEqual((o["tenant_portion"], o["subsidy_portion"], o["total_rent"]), (727.0, 158.0, 885.0))
        self.assertIn("RA 158.00 (subsidy)", o["charge_types"]); self.assertNotIn("PETFEE", o["charge_types"])

    def test_subsidy_only_is_still_rent(self):
        out = server.summarize_rent([RC(2, 11, 1100, "2021-12-01", "", "RA", "Rental Assistance")])
        self.assertEqual((out[0]["tenant_portion"], out[0]["subsidy_portion"], out[0]["total_rent"]), (0.0, 1100.0, 1100.0))

    def test_ended_charges_use_last_effective_and_asof(self):
        rows = [RC(3, 12, 800, "2020-01-01", "2020-12-31", "RC", "Rent Charge"), RC(3, 12, 850, "2021-01-01", "2021-06-30", "RC", "Rent Charge")]
        o = server.summarize_rent(rows)[0]
        self.assertEqual((o["total_rent"], o["rent_as_of"]), (850.0, "2021-06-30"))
        self.assertEqual(server.summarize_rent(rows, "2020-06-15")[0]["total_rent"], 800.0)

    def test_holdover_type_counts_after_rent_ends(self):
        rows = [RC(4, 13, 1100, "2023-01-01", "2025-06-23", "RC", "Rent Charge"), RC(4, 13, 1100, "2025-06-24", "", "U&O", "Use and Occupancy")]
        self.assertEqual(server.summarize_rent(rows)[0]["total_rent"], 1100.0)

    def test_ledger_summary(self):
        led = [{"TransactionDate": "2026-01-01T00:00:00", "TransactionType": "Charge", "Description": "Rent Charge", "Amount": 700.0, "SubsidyID": -1},
               {"TransactionDate": "2026-01-01T00:00:00", "TransactionType": "Charge", "Description": "Rental Assistance", "Amount": 300.0, "SubsidyID": -1},
               {"TransactionDate": "2026-01-05T00:00:00", "TransactionType": "Payment", "Description": "Payment Received", "Amount": -300.0, "SubsidyID": 7},
               {"TransactionDate": "2026-01-10T00:00:00", "TransactionType": "Charge", "Description": "Late Charge", "Amount": 50.0, "SubsidyID": -1}]
        s = server.summarize_ledger(led)[0]
        self.assertEqual((s["tenant_portion"], s["subsidy_portion"], s["total_rent"], s["balance"]), (700.0, 300.0, 1000.0, 750.0))
        self.assertEqual((s["last_rent_charge_date"], s["last_payment_date"], s["last_payment_from_subsidy"]), ("2026-01-01", "2026-01-05", True))


class NewestFirst(unittest.TestCase):
    ROWS = [{"TransactionDate": "2026-01-01", "Amount": 1.0, "k": "a"}, {"TransactionDate": "2026-01-02", "Amount": 2.0, "k": "b"},
            {"TransactionDate": "2026-01-02", "Amount": 3.0, "k": "c"}, {"TransactionDate": "2026-01-03", "Amount": 4.0, "k": "d"}]

    def test_running_then_newest_first_keeps_newest_with_top(self):
        rows, _, notes = server.post_process([dict(r) for r in self.ROWS], running="Amount", newest_first="TransactionDate", top=2)
        self.assertEqual([r["k"] for r in rows], ["d", "c"])
        self.assertEqual(rows[0]["RunningBalance"], 10.0)      # ending balance is on the newest row
        self.assertTrue(any("newest first" in n for n in notes))

    def test_first_per(self):
        rows = [{"u": 1, "d": "2025-01-01"}, {"u": 1, "d": "2026-01-01"}, {"u": 2, "d": "2024-01-01"}]
        out, _, notes = server.post_process(rows, sort="-d", first_per="u")
        self.assertEqual([(r["u"], r["d"]) for r in out], [(1, "2026-01-01"), (2, "2024-01-01")])

    def test_unknown_column_suggests(self):
        with self.assertRaises(server.ToolError): server.post_process([{"a": 1}], newest_first="zzz")


class VacancyAlso(unittest.TestCase):
    """vacant_units: IsVacantAsOf rows + reserved/held units that are IsVacant; reconciliation names differences."""
    STRICT = [{"UnitID": 1, "Name": "A1", "PropertyID": 10, "Property": {"Name": "Oak"}, "UnitType": {"Name": "1BR"}, "CurrentUnitStatus": {"UnitStatusType": {"Name": "Rent Ready"}}}]
    RESERVED = [{"UnitID": 2, "Name": "A2", "PropertyID": 10, "Property": {"Name": "Oak"}, "UnitType": {"Name": "1BR"}, "IsVacant": True,
                 "CurrentOccupancyStatus": {"OccupancyType": "CurrentProspectReservation"}},
                {"UnitID": 3, "Name": "A3", "PropertyID": 10, "Property": {"Name": "Oak"}, "UnitType": {"Name": "1BR"}, "IsVacant": False,
                 "CurrentOccupancyStatus": {"OccupancyType": "CurrentProspectReservation"}}]   # occupied + reserved for renewal: NOT vacant

    def run_vu(self, params=None):
        def f(path, prm, *a, **k):
            rows = self.STRICT if "IsVacantAsOf" in prm.get("filters", "") else self.RESERVED
            return rows, len(rows), 1, 200
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"RM_OUT": td}), \
             mock.patch.object(server, "find_resource", lambda n: {"model": "Unit", "path": "/Units"}), \
             mock.patch.object(server, "validate", lambda m, fl, em, flt, ob: (fl, em, flt, ("", ""), [])), \
             mock.patch.object(server, "fetch_ordered", f), \
             mock.patch.object(server, "resolve_property_ids", lambda v: ([], "")), \
             mock.patch.object(server, "all_scope_label", lambda: "All test properties"):
            return server.run_recipe_result("vacant_units", params or {})

    def test_reserved_included_and_flagged_not_excluded(self):
        res = self.run_vu()
        self.assertEqual(res["facts"]["vacant"], 2)
        ids = {r["UnitID"]: r for r in res["rows"]}
        self.assertEqual(set(ids), {1, 2})
        self.assertEqual(ids[2]["occupancy_flag"], "reserved by prospect"); self.assertEqual(ids[1]["status_flag"], "Rent Ready")

    def test_past_date_uses_strict_filter_only(self):
        self.assertEqual(self.run_vu({"as_of": "2020-01-01"})["facts"]["vacant"], 1)

    def test_reconcile_lists_both_directions(self):
        rows = [{"UnitID": 1, "unit": "A1"}, {"UnitID": 9, "unit": "Z9", "property": "Elm"}]
        other = {"rows": [{"UnitID": 1, "unit": "A1"}, {"UnitID": 2, "unit": "A2", "property": "Oak", "unit_type": "EventRoom"}]}
        with mock.patch.object(server, "run_recipe_result", lambda *a, **k: other):
            txt = server.reconcile_recipes({"recipe": "vacant_units", "definition": "d"}, {"property": "", "as_of": ""}, rows)
        self.assertIn("NOT here (1): Oak A2", txt); self.assertIn("unit type EventRoom", txt)
        self.assertIn("Here but NOT in vacant_units (1): Elm Z9", txt)


if __name__ == "__main__":
    unittest.main()
