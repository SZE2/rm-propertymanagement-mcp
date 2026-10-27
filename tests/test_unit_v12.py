#!/usr/bin/env python3
"""Offline unit tests (v12): row cap + CAPPED line, newest-slice reads, Rent Manager OutOfMemory handling (smaller pages, partial result,
clear error), built-in report suggestions (find_recipe / search_models / CAPPED / OOM text), tenant_ledger for one or several tenants with
dates. Synthetic data only; the API is stubbed (no network)."""
import os, sys, json, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server
from offline_fixture import setUpModule

OOM_BODY = {"DeveloperMessage": "Exception of type 'System.OutOfMemoryException' was thrown.", "UserMessage": "Unspecified Error"}


def make_api(n, oom_above=None, oom_from_row=None, oom_below_row=None, size_oom=False):
    """Stub for server.api over a table of n rows {'i': k}. oom_above: pageSize above it answers OutOfMemory; oom_from_row / oom_below_row:
    requests starting at/after (before) that row always do; size_oom: even pageSize=1 does."""
    rows = [{"i": k} for k in range(n)]
    calls = []
    def api(path, params=None, timeout=None):
        params = dict(params or {})
        ps, pn = int(params.get("pageSize", 1000)), int(params.get("pageNumber", 1))
        calls.append((ps, pn, dict(params)))
        start = (pn - 1) * ps
        if size_oom and ps == 1: return 500, {}, OOM_BODY
        if (oom_above is not None and ps > oom_above) or (oom_from_row is not None and ps > 1 and start >= oom_from_row) \
                or (oom_below_row is not None and ps > 1 and start < oom_below_row):
            return 500, {}, OOM_BODY
        chunk = rows[start: start + ps]
        return (200 if chunk else 204), {"x-total-results": str(n)}, (chunk or None)
    api.calls = calls
    return api


class Limits(unittest.TestCase):
    def test_default_explicit_and_ceiling(self):
        self.assertEqual(server.row_limit(0), server.MAX_ROWS)
        self.assertEqual(server.MAX_ROWS, 5000)
        self.assertEqual(server.row_limit(8000), 8000)
        self.assertEqual(server.row_limit(100000), server.MAX_ROWS_HARD)
        self.assertEqual(server.MAX_ROWS_HARD, 20000)
        self.assertEqual(server.row_limit(0, hard=True), server.MAX_ROWS_HARD)
        with mock.patch.object(server, "MAX_ROWS", 100), mock.patch.object(server, "MAX_ROWS_HARD", 300):
            self.assertEqual(server.row_limit(0), 100)
            self.assertEqual(server.row_limit(250), 250)
            self.assertEqual(server.row_limit(999), 300)

    def test_cap_line_text(self):
        t = server.cap_line(5000, 12345, "newest", "/Charges")
        self.assertTrue(t.startswith("CAPPED: showing the newest 5000 of 12345 rows."), t)
        for w in ("More exist", "narrow by date range or property/tenant", "report 30", "max_records", "RM_MAX_ROWS"):
            self.assertIn(w, t)
        self.assertIn("unknown number", server.cap_line(10, 0, "first", "/Units"))
        self.assertNotIn("or use", server.cap_line(10, 99, "oldest", "/Units"))     # no report for this resource: no suggestion


class Paging(unittest.TestCase):
    def test_head_read_is_capped_and_total_known(self):
        api = make_api(12000)
        with mock.patch.object(server, "api", api):
            rows, total, pages, s = server.fetch("/Things", {"fields": "i"}, max_records=2500, page_size=1000)
        self.assertEqual((len(rows), total, s), (2500, 12000, 200))
        self.assertEqual([r["i"] for r in rows[:3]], [0, 1, 2])
        self.assertFalse(rows.newest)

    def test_newest_slice_reads_the_last_pages(self):
        api = make_api(12000)
        with mock.patch.object(server, "api", api):
            params = {"fields": "i", "orderingOptions": ""}
            rows, total, pages, s = server.fetch("/Things", params, max_records=5000, newest_col="i")
        self.assertEqual(total, 12000)
        self.assertTrue(rows.newest)
        self.assertEqual((len(rows), rows[0]["i"], rows[-1]["i"]), (5000, 7000, 11999))
        self.assertEqual(params["orderingOptions"], "i")           # sorted ascending by the date field, last pages read
        sizing = api.calls[0]
        self.assertEqual(sizing[0], 1)
        self.assertNotIn("orderingOptions", sizing[2])             # the count call is never sorted (slow)
        self.assertTrue(all(c[2].get("orderingOptions") == "i" for c in api.calls[1:]))
        self.assertLessEqual(len(api.calls), 4)                    # sizing + 2 pages (+ nothing wasted)

    def test_small_table_is_read_completely_even_when_newest_wanted(self):
        api = make_api(300)
        with mock.patch.object(server, "api", api):
            rows, total, pages, s = server.fetch("/Things", {"fields": "i"}, max_records=5000, newest_col="i")
        self.assertEqual((len(rows), total, rows.newest), (300, 300, False))

    def test_transactions_are_read_in_api_order_without_a_sort(self):
        api = make_api(500)
        with mock.patch.object(server, "api", api):
            params = {"fields": "i", "orderingOptions": "TransactionDate"}
            rows, total, pages, s = server.fetch("/Tenants/5/Transactions", params, max_records=100, newest_col="TransactionDate")
        self.assertEqual((rows[0]["i"], rows[-1]["i"], rows.newest, rows.chrono), (400, 499, True, True))
        self.assertTrue(all("orderingOptions" not in c[2] for c in api.calls))   # that resource answers 404 to a sort
        self.assertEqual(params["orderingOptions"], "")


class OutOfMemory(unittest.TestCase):
    def test_smaller_pages_until_it_works(self):
        api = make_api(1200, oom_above=300)
        with mock.patch.object(server, "api", api):
            rows, total, pages, s = server.fetch("/Things", {"fields": "i"}, max_records=1200, page_size=1000)
        self.assertEqual([r["i"] for r in rows], list(range(1200)))      # nothing lost, nothing doubled
        sizes = [c[0] for c in api.calls]
        self.assertEqual(sizes[:3], [1000, 500, 250])                     # halved: 1000 -> 500 -> 250
        self.assertEqual(rows.partial, "")

    def test_ladder_goes_down_to_fifty(self):
        api = make_api(200, oom_above=60)
        with mock.patch.object(server, "api", api):
            rows, total, pages, s = server.fetch("/Things", {"fields": "i"}, max_records=200, page_size=1000)
        self.assertEqual(len(rows), 200)
        tried = []
        for c in api.calls:
            if c[0] not in tried: tried.append(c[0])
        self.assertEqual(tried[:5], [1000, 500, 250, 100, 50])

    def test_partial_result_keeps_pages_already_read(self):
        api = make_api(3000, oom_from_row=1000)
        with mock.patch.object(server, "api", api):
            rows, total, pages, s = server.fetch("/Things", {"fields": "i"}, max_records=3000, page_size=1000, partial_ok=True)
        self.assertEqual(len(rows), 1000)
        self.assertEqual(total, 3000)
        self.assertIn("ran out of memory", rows.partial)
        with mock.patch.object(server, "api", make_api(3000, oom_from_row=1000)):
            with self.assertRaises(server.RMOutOfMemory) as cm:
                server.fetch("/Things", {"fields": "i"}, max_records=3000, page_size=1000)
        self.assertEqual(len(cm.exception.rows), 1000)

    def test_error_when_nothing_could_be_read(self):
        with mock.patch.object(server, "api", make_api(5000, oom_above=0)):
            with self.assertRaises(server.ToolError) as cm:
                server.fetch("/Tenants/5/Transactions", {"fields": "i"}, max_records=2000, page_size=1000)
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("Rent Manager ran out of memory on this request (its limit, not a bug here). Narrow it:"), msg)
        for w in ("date range", "property", "tenant", "fewer embeds", "Built-in report that covers this", "report 30"):
            self.assertIn(w, msg)

    def test_newest_slice_keeps_whole_newest_pages_when_older_ones_fail(self):
        api = make_api(12000, oom_below_row=10000)
        with mock.patch.object(server, "api", api):
            rows, total, pages, s = server.fetch("/Things", {"fields": "i"}, max_records=5000, newest_col="i", partial_ok=True)
        self.assertTrue(rows.newest)
        self.assertEqual((rows[0]["i"], rows[-1]["i"], len(rows)), (10000, 11999, 2000))
        self.assertIn("ran out of memory", rows.partial)

    def test_report_oom_message(self):
        rep = server.report_by("30")
        with mock.patch.object(server, "api", lambda path, params=None, timeout=None: (500, {}, OOM_BODY)):
            with self.assertRaises(server.ToolError) as cm:
                server.run_report_raw(rep, {"StartDate": "2026-01-01"})
        msg = str(cm.exception)
        self.assertIn("ran out of memory", msg)
        self.assertIn("PropertyIDs", msg); self.assertIn("one property at a time", msg); self.assertIn("date range", msg)


class QueryLevel(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        mock.patch.dict(os.environ, {"RM_OUT": self.td.name}).start()
        mock.patch.object(server, "all_scope_label", lambda: "All properties").start()
        self.addCleanup(mock.patch.stopall); self.addCleanup(self.td.cleanup)

    def test_capped_line_is_first_and_names_newest(self):
        api = make_api(12000)
        with mock.patch.object(server, "api", api):
            out = server.t_query("Payments", fields="i,TransactionDate".replace("i,", "PaymentID,"))
        self.assertTrue(out.startswith("CAPPED: showing the newest 5000 of 12000 rows."), out[:200])
        self.assertIn("report 25", out)
        self.assertIn("/Payments: 5000 of 12000 record(s)", out)

    def test_explicit_max_records_raises_but_stops_at_the_ceiling(self):
        api = make_api(30000)
        with mock.patch.object(server, "api", api):
            out = server.t_query("Payments", fields="PaymentID,TransactionDate", max_records=100000)
        self.assertTrue(out.startswith("CAPPED: showing the newest 20000 of 30000 rows."), out[:200])
        with mock.patch.object(server, "api", make_api(30000)):
            out = server.t_query("Payments", fields="PaymentID,TransactionDate", max_records=8000)
        self.assertTrue(out.startswith("CAPPED: showing the newest 8000 of 30000 rows."), out[:200])

    def test_no_capped_line_when_everything_fits(self):
        with mock.patch.object(server, "api", make_api(40)):
            out = server.t_query("Payments", fields="PaymentID,TransactionDate")
        self.assertNotIn("CAPPED", out)

    def test_oldest_first_request_is_honoured(self):
        with mock.patch.object(server, "api", make_api(9000)):
            out = server.t_query("Payments", fields="PaymentID,TransactionDate", orderby="TransactionDate")
        self.assertTrue(out.startswith("CAPPED: showing the oldest 5000 of 9000 rows."), out[:200])

    def test_without_a_date_column_it_says_first(self):
        with mock.patch.object(server, "api", make_api(9000)):
            out = server.t_query("Charges", fields="ChargeID,Amount")
        self.assertTrue(out.startswith("CAPPED: showing the first 5000 of 9000 rows."), out[:200])
        self.assertIn("report 30", out)

    def test_oom_everywhere_is_a_clear_error_with_a_report(self):
        with mock.patch.object(server, "api", make_api(9000, oom_above=0)):
            with self.assertRaises(server.ToolError) as cm:
                server.t_query("Charges", fields="ChargeID,Amount")
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("Rent Manager ran out of memory on this request (its limit, not a bug here)."), msg)
        self.assertIn("report 30", msg)

    def test_oom_while_sizing_is_the_same_clear_error(self):
        with mock.patch.object(server, "api", make_api(9000, size_oom=True)):
            with self.assertRaises(server.ToolError) as cm:
                server.t_query("Payments", fields="PaymentID,TransactionDate")
        self.assertTrue(str(cm.exception).startswith("Rent Manager ran out of memory"), str(cm.exception))
        self.assertIn("report 25", str(cm.exception))

    def test_partial_result_is_flagged_first(self):
        with mock.patch.object(server, "api", make_api(3000, oom_from_row=1000)):
            out = server.t_query("Charges", fields="ChargeID,Amount", max_records=3000, page_size=1000)
        self.assertTrue(out.startswith("PARTIAL: only the first 1000 of 3000 rows were read"), out[:200])
        self.assertIn("ran out of memory", out.split("\n")[0])


class Suggestions(unittest.TestCase):
    def test_concepts_has_the_sections(self):
        ids = {c["id"] for c in server.concepts().get("report_suggestions", [])}
        self.assertTrue({"ledger", "charges", "payments", "history", "recurring", "leases", "gl"} <= ids)
        for c in server.concepts()["report_suggestions"]:
            self.assertTrue(c["words"] and c["resources"] and c["short"] and c["text"], c["id"])

    def test_find_recipe_for_full_ledger(self):
        for q in ("full tenant ledger for one or several tenants at a property", "tenant statement", "charges and payments for a tenant", "tenant history ledger"):
            out = server.t_find_recipe(q)
            self.assertIn("report 30", out, q)
            self.assertIn("ChargeDetail", out, q)
            self.assertIn("tenant_ledger", out, q)
            self.assertIn("no built-in report takes a tenant filter", out.lower(), q)

    def test_search_models_shows_them_too(self):
        out = server.t_search_models("full tenant ledger for one or several tenants at a property")
        self.assertIn("Built-in report suggestion (ledger)", out)
        self.assertIn("report 30", out)
        self.assertIn("Tenants/{id}/Transactions", out)

    def test_other_topics(self):
        self.assertIn("report 25", server.t_find_recipe("payments received"))
        self.assertIn("report 781", "\n".join(server.suggestion_lines("recurring charges")))
        self.assertIn("report 18", "\n".join(server.suggestion_lines("tenant history notes")))
        self.assertIn("report 51", "\n".join(server.suggestion_lines("general ledger")))
        self.assertIn("report 76", "\n".join(server.suggestion_lines("lease expiration")))

    def test_path_hints(self):
        self.assertIn("report 30", server.report_hint("/Tenants/5/Transactions"))
        self.assertIn("report 25", server.report_hint("/Payments"))
        self.assertIn("report 18", server.report_hint("/HistoryNotes"))
        self.assertEqual(server.report_hint("/Units"), "")

    def test_tenant_recipe_is_findable_for_ledger_words(self):
        self.assertEqual(server.recipe_search("tenant ledger for several tenants")[0][1]["name"], "tenant_ledger")


def ledger_api(tables, balances):
    """Stub for /Tenants/{id}/Transactions (paged, filtered by date) and /Tenants/{id} (Balance)."""
    log = []
    def api(path, params=None, timeout=None):
        params = dict(params or {})
        log.append((path, params))
        parts = path.strip("/").split("/")
        tid = int(parts[1])
        if len(parts) == 2:
            return 200, {}, {"TenantID": tid, "Balance": balances[tid]}
        rows = list(tables[tid])
        for clause in [c for c in (params.get("filters") or "").split(";") if c]:
            f, op, v = clause.split(",", 2)
            v = v[:10]
            rows = [r for r in rows if (r["TransactionDate"][:10] >= v if op == "ge" else r["TransactionDate"][:10] <= v if op == "le"
                                        else r["TransactionDate"][:10] > v if op == "gt" else r["TransactionDate"][:10] < v)]
        ps, pn = int(params.get("pageSize", 1000)), int(params.get("pageNumber", 1))
        chunk = rows[(pn - 1) * ps: pn * ps]
        return (200 if chunk else 204), {"x-total-results": str(len(rows))}, (chunk or None)
    api.log = log
    return api


def ledger_rows(n, amount=100.0, month="2026-07"):
    return [{"TransactionDate": "%s-%02dT00:00:00" % (month, k + 1), "TransactionType": "Charge", "Description": "Rent Charge", "Reference": "",
             "Comment": "c%d" % k, "Amount": amount, "Unit": {"Name": "1A"}} for k in range(n)]


class TenantLedger(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        mock.patch.dict(os.environ, {"RM_OUT": self.td.name}).start()
        mock.patch.object(server, "all_scope_label", lambda: "All properties").start()
        mock.patch.object(server, "resolve_property_ids", lambda v: ([], None)).start()
        self.addCleanup(mock.patch.stopall); self.addCleanup(self.td.cleanup)

    def run_ledger(self, params, tables, balances, show=40):
        api = ledger_api(tables, balances)
        with mock.patch.object(server, "api", api):
            out = server.t_run_recipe("tenant_ledger", params, show=show)
        return out, api

    def test_recipe_declares_the_new_params(self):
        r = next(x for x in server.load_recipes() if x["name"] == "tenant_ledger")
        for k in ("tenant", "since", "until", "oldest_first", "max_rows"):
            self.assertIn(k, r["params"])
        self.assertEqual(r["params"]["max_rows"]["default"], 1000)
        self.assertIn("tenants", r["params"]["tenant"]["aliases"])

    def test_one_tenant_dates_are_filters(self):
        out, api = self.run_ledger({"tenant": 1, "since": "2026-07-03", "until": "2026-07-07"}, {1: ledger_rows(10)}, {1: 1000.0})
        flt = [p.get("filters") for path, p in api.log if path.endswith("Transactions")]
        self.assertTrue(any("TransactionDate,ge,2026-07-03" in (f or "") and "TransactionDate,le,2026-07-07" in (f or "") for f in flt), flt)
        self.assertNotIn("CAPPED", out)
        self.assertIn("5 ledger transactions", out)
        # window rows 3..7 (500.00); balance 1000 with 3 later rows (300.00) and 2 earlier rows -> opening 200, ending 700
        self.assertIn("ending running balance $700.00", out)

    def test_several_tenants_as_a_list_one_table_per_call(self):
        out, api = self.run_ledger({"tenants": [1, 2]}, {1: ledger_rows(4), 2: ledger_rows(2, 50.0)}, {1: 400.0, 2: 100.0})
        paths = {p for p, _ in api.log if p.endswith("Transactions")}
        self.assertEqual(paths, {"/Tenants/1/Transactions", "/Tenants/2/Transactions"})
        self.assertIn("6 ledger transactions for 2 tenants", out)
        self.assertIn("Tenant|TransactionDate", out)
        self.assertIn("ID 1 $400.00", out); self.assertIn("ID 2 $100.00", out)

    def test_semicolon_string_is_a_list_and_comma_is_part_of_one_name(self):
        self.assertEqual(server.tenant_terms("Smith, John; Doe, Jane"), ["Smith, John", "Doe, Jane"])
        self.assertEqual(server.tenant_terms("Smith, John"), ["Smith, John"])
        self.assertEqual(server.tenant_terms(["5", 6]), ["5", "6"])
        self.assertEqual(server.tenant_terms('["a","b"]'), ["a", "b"])
        with self.assertRaises(server.ToolError): server.tenant_terms(" ; ")

    def test_default_is_newest_1000_and_capped_says_so_first(self):
        out, api = self.run_ledger({"tenant": 1, "max_rows": 4}, {1: ledger_rows(10)}, {1: 1000.0}, show=3)
        self.assertTrue(out.startswith("CAPPED: showing the newest 4 of 10 rows."), out[:160])
        self.assertIn("report 30", out)
        self.assertIn("older rows are not listed", out)
        self.assertIn("ledger's own", out)
        first = [l for l in out.split("\n") if l.startswith("2026-")][0]
        self.assertTrue(first.startswith("2026-07-10|"), first)          # newest first
        self.assertIn("|1000.00|", first)                                 # running balance = the ledger's own balance at that row
        self.assertIn("ending running balance $1,000.00", out)
        r = next(x for x in server.load_recipes() if x["name"] == "tenant_ledger")
        self.assertEqual(r["params"]["max_rows"]["default"], 1000)

    def test_opening_balance_unknown_is_said(self):
        api = ledger_api({1: ledger_rows(10)}, {1: 1000.0})
        base = api
        def api2(path, params=None, timeout=None):
            if path == "/Tenants/1": return 500, {}, {"Message": "boom"}
            return base(path, params, timeout)
        with mock.patch.object(server, "api", api2):
            out = server.t_run_recipe("tenant_ledger", {"tenant": 1, "max_rows": 4})
        self.assertIn("Opening balance could not be read", out)

    def test_oldest_first_cap_says_oldest(self):
        out, api = self.run_ledger({"tenant": 1, "max_rows": 4, "oldest_first": True}, {1: ledger_rows(10)}, {1: 1000.0})
        self.assertTrue(out.startswith("CAPPED: showing the oldest 4 of 10 rows."), out[:160])

    def test_too_many_tenants(self):
        with self.assertRaises(server.ToolError) as cm:
            with mock.patch.object(server, "api", ledger_api({}, {})):
                server.t_run_recipe("tenant_ledger", {"tenant": list(range(1, 30))})
        self.assertIn("At most 25", str(cm.exception))
        self.assertIn("report 30", str(cm.exception))


class Versions(unittest.TestCase):
    def test_version_and_docs(self):
        import os, re as _re
        _cl = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md"), encoding="utf-8").read()
        _newest = _re.search(r"^## \[(\d+\.\d+\.\d+(?:-beta)?)\]", _cl, _re.M).group(1)
        self.assertEqual(server.VERSION, _newest)
        ki = open(os.path.join(HERE, "KNOWN-ISSUES.md"), encoding="utf-8").read()
        self.assertIn("RM_MAX_ROWS", ki); self.assertIn("RM_MAX_ROWS_HARD", ki)
        cl = open(os.path.join(HERE, "CHANGELOG.md"), encoding="utf-8").read()
        self.assertIn("CAPPED", cl)

    def test_descriptions_stay_within_caps(self):
        self.assertLessEqual(len(server.INSTRUCTIONS), 1500)
        for t in server.tool_list(): self.assertLessEqual(len(t["description"]), 1900, t["name"])


if __name__ == "__main__":
    unittest.main()
