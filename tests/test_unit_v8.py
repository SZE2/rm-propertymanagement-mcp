#!/usr/bin/env python3
"""Offline unit tests (v8, sage review fixes): ledger rent classification with the generated charge_types file present, rent summary
completeness, newest-lease ordering, in()/bt normalisation, past-date vacancy wording, GET-only guard, call-log rotation + masking.
Generic data only."""
import os, sys, json, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server, rm

LEDGER = [{"TransactionDate": "2026-01-01T00:00:00", "TransactionType": "Charge", "Description": "Rent Charge", "Amount": 1000.0, "SubsidyID": -1},
          {"TransactionDate": "2026-01-01T00:00:00", "TransactionType": "Charge", "Description": "Rental Assistance", "Amount": 500.0, "SubsidyID": -1},
          {"TransactionDate": "2026-01-03T00:00:00", "TransactionType": "Charge", "Description": "Late Charge", "Amount": 50.0, "SubsidyID": -1}]


def RC(tid, uid, amt, frm, to, code, desc):
    return {"EntityKeyID": tid, "UnitID": uid, "Amount": amt, "FromDate": frm + "T00:00:00", "ToDate": (to + "T00:00:00") if to else None,
            "ChargeType.Name": code, "ChargeType.Description": desc}


class LedgerWithFile(unittest.TestCase):
    """The generated file stores CODES; ledger rows only carry descriptions. The file is NOT patched away here."""
    def check(self, content):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "ct.json")
            with open(fp, "w") as fh: json.dump(content, fh)
            with mock.patch.object(server, "CHARGE_TYPES_FILE", fp):
                self.assertTrue(server.load_charge_families())
                s = server.summarize_ledger(LEDGER)[0]
                self.assertEqual((s["tenant_portion"], s["subsidy_portion"], s["total_rent"]), (1000.0, 500.0, 1500.0))
                self.assertEqual(s["last_rent_charge_date"], "2026-01-01")
                self.assertEqual(server.rent_type_label("Rent Charge"), "rent (tenant portion)")
                self.assertEqual(server.rent_type_label("Rental Assistance"), "rent (subsidy portion)")
                self.assertEqual(server.rent_type_label("Late Charge"), "")
                self.assertEqual(server.rent_type_label("Rent Charge", "Payment"), "")

    def test_codes_only_file(self):
        self.check({"rent": ["RC"], "subsidy": ["RA"]})

    def test_file_with_descriptions(self):
        self.check({"rent": ["RC"], "subsidy": ["RA"], "rent_descriptions": ["Rent Charge"], "subsidy_descriptions": ["Rental Assistance"]})

    def test_description_with_suffix_and_codes_still_match(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "ct.json")
            with open(fp, "w") as fh:
                json.dump({"rent": ["RC"], "subsidy": ["RA"], "rent_descriptions": ["Rent Charge"], "subsidy_descriptions": []}, fh)
            with mock.patch.object(server, "CHARGE_TYPES_FILE", fp):
                self.assertEqual(server.charge_family("x", "Rent Charge 10/2026"), "rent")
                self.assertEqual(server.charge_family("RC", ""), "rent")
                self.assertIsNone(server.charge_family("PETFEE", "Pet Rent"))

    def test_write_charge_types_stores_descriptions(self):
        types = [{"Name": "RC", "Description": "Rent Charge"}, {"Name": "RA", "Description": "Rental Assistance"}, {"Name": "PET", "Description": "Pet Rent"}]
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "local", "ct.json")
            with mock.patch.object(server, "CHARGE_TYPES_FILE", fp), mock.patch.object(server, "LOCAL", os.path.dirname(fp)), \
                 mock.patch.object(server, "api", lambda *a, **k: (200, {}, types)):
                self.assertEqual(server.write_charge_types(), 2)
                with open(fp) as fh: d = json.load(fh)
                self.assertEqual((d["rent"], d["subsidy"], d["rent_descriptions"], d["subsidy_descriptions"]), (["RC"], ["RA"], ["Rent Charge"], ["Rental Assistance"]))
                self.assertEqual(server.write_charge_types(), -1)   # kept


class Classifier(unittest.TestCase):
    def test_broader_defaults(self):
        c = server.classify_charge_type
        for code, desc in [("MR", "Monthly Rent"), ("x", "Residential Rent"), ("x", "Base Rent"), ("x", "Rent Charge 10/2026")]:
            self.assertEqual(c(code, desc), "rent", desc)
        for code, desc in [("HCV", "Housing Choice Voucher"), ("PHA", "PHA payment"), ("S8", "S8")]:
            self.assertEqual(c(code, desc), "subsidy", desc)
        for code, desc in [("P", "Pet Rent"), ("G", "Garage Rent"), ("E", "Employee Rent Concession"), ("R", "Renters Insurance"),
                           ("B", "Hall Rental Income"), ("L", "Late Fee"), ("D", "Rent Deposit"), ("C", "Rent Credit")]:
            self.assertIsNone(c(code, desc), desc)


class RentSummary(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(server, "CHARGE_TYPES_FILE", os.path.join(tempfile.gettempdir(), "no_such_charge_types_v8.json"))
        p.start(); self.addCleanup(p.stop)

    def test_unmatched_tenant_reported_and_not_counted_listed(self):
        info = {}
        out = server.summarize_rent([RC(1, 10, 1000, "2024-01-01", "", "RC", "Rent Charge"), RC(2, 11, 40, "2024-01-01", "", "PETFEE", "Pet Rent"),
                                     RC(1, 10, 40, "2024-01-01", "", "PETFEE", "Pet Rent")], "", None, info)
        by = {r["TenantID"]: r for r in out}
        self.assertEqual(set(by), {1, 2})
        self.assertEqual(by[2]["total_rent"], 0.0); self.assertIn("no rent-type charge", by[2]["note"]); self.assertIn("PETFEE", by[2]["note"])
        self.assertEqual(info["not_counted"], [("PETFEE", "Pet Rent", 2)])

    def test_stale_open_ended_charge_of_moved_out_tenant(self):
        info = {}
        out = server.summarize_rent([RC(1, 10, 900, "2020-01-01", "", "RC", "Rent Charge"), RC(2, 11, 800, "2020-01-01", "", "RC", "Rent Charge")],
                                    "", {"1": "Past", "2": "Current"}, info)
        by = {r["TenantID"]: r for r in out}
        self.assertIn("STALE", by[1]["note"]); self.assertEqual(by[2]["note"], ""); self.assertEqual(info["stale"], 1)

    def test_asof_without_charge_not_dropped(self):
        out = server.summarize_rent([RC(1, 10, 900, "2022-01-01", "", "RC", "Rent Charge")], "2021-06-01")
        self.assertEqual(len(out), 1); self.assertIn("no rent charge in effect", out[0]["note"])

    def test_rent_summary_raises_cap_and_says_partial(self):
        if not server.SI.load_index(): self.skipTest("no spec index")
        seen = {}
        def f(path, prm, mx, *a, **k):
            seen["max"] = mx
            rows = [{"EntityKeyID": i, "UnitID": i, "Amount": 100, "FromDate": "2024-01-01T00:00:00", "ToDate": None,
                     "ChargeType": {"Name": "RC", "Description": "Rent Charge"}} for i in range(600)]
            return rows, 650, 1, 200
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"RM_OUT": td}), \
             mock.patch.object(server, "fetch_ordered", f), mock.patch.object(server, "tenant_statuses", lambda ids: {}):
            txt = server.t_query("RecurringCharges", filters="UnitID,in,(1,2)", summary="rent")
        self.assertGreaterEqual(seen["max"], 20000)
        self.assertIn("PARTIAL", txt)


class NewestLease(unittest.TestCase):
    ROWS = [{"u": 1, "MoveOutDate": "2025-05-01"}, {"u": 1, "MoveOutDate": None}, {"u": 2, "MoveOutDate": "2024-01-01"},
            {"u": 2, "MoveOutDate": "2023-01-01"}, {"u": None, "MoveOutDate": "2020-01-01"}, {"u": None, "MoveOutDate": "2021-01-01"}]

    def test_open_lease_first_per_unit(self):
        out, _, notes = server.post_process([dict(r) for r in self.ROWS], sort="-MoveOutDate", first_per="u")
        by = {r["u"]: r for r in out if r["u"] is not None}
        self.assertIsNone(by[1]["MoveOutDate"]); self.assertEqual(by[2]["MoveOutDate"], "2024-01-01")
        self.assertEqual(len([r for r in out if r["u"] is None]), 2)   # keyless rows are not merged
        self.assertTrue(any("without a key" in n for n in notes))

    def test_newest_first_puts_open_end_date_first(self):
        out, _, _ = server.post_process([dict(r) for r in self.ROWS], newest_first="MoveOutDate")
        self.assertIsNone(out[0]["MoveOutDate"])

    def test_other_dates_still_blank_last(self):
        rows = [{"d": None}, {"d": "2025-01-01"}, {"d": "2026-01-01"}]
        out, _, _ = server.post_process(rows, sort="-d")
        self.assertEqual([r["d"] for r in out], ["2026-01-01", "2025-01-01", None])

    def test_capped_fetch_says_newest_missing_and_ignored_note(self):
        if not server.SI.load_index(): self.skipTest("no spec index")
        def f(path, prm, mx, *a, **k): return [{"TransactionDate": "2020-01-01T00:00:00", "Amount": 1.0} for _ in range(5)], 25000, 1, 200
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"RM_OUT": td}), mock.patch.object(server, "fetch_ordered", f):
            txt = server.t_query("Tenants/5/Transactions", fields="TransactionDate,Amount", most_recent_first=True)
            self.assertTrue(txt.startswith("CAPPED: showing the first 5 of 25000 rows"), txt[:120])
            self.assertIn("newest rows could not be selected", txt)
            txt = server.t_query("Tenants/5/Transactions", fields="TransactionDate,Amount", most_recent_first=True, sort="Amount")
            self.assertIn("most_recent_first ignored: sort given", txt)


class InNormalisation(unittest.TestCase):
    def test_empty_list_is_an_error(self):
        for f in ("UnitID,in,()", "UnitID,in,", "A,eq,1;UnitID,in,;B,eq,2", "UnitID,ni,( )"):
            with self.assertRaises(server.ToolError, msg=f): server.normalize_in(f)

    def test_quoted_values_preserved(self):
        self.assertEqual(server.normalize_in('Name,in,("Smith, John",Doe)'), 'Name,in,("Smith, John",Doe)')
        self.assertEqual(server.normalize_in("Name,in,('A; B' ; C)"), "Name,in,('A; B',C)")
        self.assertEqual(server.split_top(server.normalize_in('Name,in,("Smith, John",Doe);X,eq,1')), ['Name,in,("Smith, John",Doe)', "X,eq,1"])

    def test_bt_not_split_on_semicolon(self):
        self.assertEqual(server.normalize_in("TransactionDate,bt,2026-01-01;2026-02-01"), "TransactionDate,bt,(2026-01-01,2026-02-01)")
        self.assertEqual(server.normalize_in("Amount,bt,(5;9)"), "Amount,bt,(5,9)")
        self.assertEqual(server.split_top(server.normalize_in("Amount,bt,5;9;UnitID,eq,3")), ["Amount,bt,(5,9)", "UnitID,eq,3"])
        with self.assertRaises(server.ToolError): server.normalize_in("Amount,bt,(1,2,3)")


class PastVacancy(unittest.TestCase):
    STRICT = [{"UnitID": 1, "Name": "A1", "PropertyID": 10, "Property": {"Name": "Oak"}, "UnitType": {"Name": "1BR"}}]
    RESERVED = [{"UnitID": 2, "Name": "A2", "PropertyID": 10, "Property": {"Name": "Oak"}, "UnitType": {"Name": "1BR"}, "IsVacant": True,
                 "CurrentOccupancyStatus": {"OccupancyType": "CurrentProspectReservation"}}]

    def test_past_as_of_states_strict_definition_and_count_difference(self):
        def f(path, prm, *a, **k):
            rows = self.STRICT if "IsVacantAsOf" in prm.get("filters", "") else self.RESERVED
            return rows, len(rows), 1, 200
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"RM_OUT": td}), \
             mock.patch.object(server, "find_resource", lambda n: {"model": "Unit", "path": "/Units"}), \
             mock.patch.object(server, "validate", lambda m, fl, em, flt, ob: (fl, em, flt, ("", ""), [])), \
             mock.patch.object(server, "fetch_ordered", f), mock.patch.object(server, "resolve_property_ids", lambda v: ([], "")), \
             mock.patch.object(server, "all_scope_label", lambda: "All test properties"):
            res = server.run_recipe_result("vacant_units", {"as_of": "2020-01-01"})
        txt = json.dumps(res, default=str)
        self.assertIn("STRICT definition", txt); self.assertIn("would add 1 unit", txt); self.assertIn("NOT comparable", txt)


class ReadOnlyGuard(unittest.TestCase):
    def test_only_get_and_login_post(self):
        for m, p in [("POST", "/Tenants"), ("DELETE", "/Tenants/1"), ("PUT", "/Units/1"), ("PATCH", "/x"), ("POST", "/Authentication/Other")]:
            with self.assertRaises(rm.RMError, msg=m + p): rm._req(m, p)

    def test_allowed_methods_pass_the_guard(self):
        with mock.patch.object(rm.urllib.request, "urlopen", side_effect=RuntimeError("net")), mock.patch.dict(rm.ENV, {"RM_COMPANY": "x"}):
            for m, p in [("GET", "/Tenants"), ("POST", "/Authentication/AuthorizeUser")]:
                with self.assertRaises(RuntimeError): rm._req(m, p)


class CallLog(unittest.TestCase):
    def test_rotation_and_masking(self):
        with tempfile.TemporaryDirectory() as td:
            lg = os.path.join(td, "log.jsonl")
            with mock.patch.dict(os.environ, {"RM_MCP_LOG": lg}), mock.patch.object(server, "LOG_MAX_BYTES", 400):
                for i in range(12):
                    server.call_log("query", {"filters": "Email,eq,pat@example.com", "q": "call 203-555-0142 or 123456789012", "n": i}, 0.1, True, 5,
                                    "ambiguous: pat@example.com 203-555-0142")
            self.assertTrue(os.path.exists(os.path.join(td, "log.1.jsonl")))
            for fn in ("log.jsonl", "log.1.jsonl"):
                with open(os.path.join(td, fn), encoding="utf-8") as fh: t = fh.read()
                for bad in ("pat@example", "203-555", "123456789012"): self.assertNotIn(bad, t)
                self.assertIn("[masked]", t)
            self.assertLess(os.path.getsize(lg), 2000)


if __name__ == "__main__":
    unittest.main()
