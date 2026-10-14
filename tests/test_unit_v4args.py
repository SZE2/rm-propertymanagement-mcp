#!/usr/bin/env python3
"""v4 item 4: Haiku argument-shape table (offline, mocked api). Plausible shapes run; failures carry a corrected example."""
import os, sys, json, datetime, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server

PROPS = [{"PropertyID": 12, "Name": "Oak Court", "ShortName": "OAK", "IsActive": True},
         {"PropertyID": 7, "Name": "Elm Terrace", "ShortName": "ELM", "IsActive": True}]
TODAY = datetime.date(2026, 10, 14)

def fake_api(path, params=None, *a, **k):
    if path.startswith("/Properties"): return 200, {}, PROPS
    return 204, {}, None

def run(args):
    """Call run_recipe through call_tool; return the report parameters the recipe would send."""
    seen = {}
    def fake_report(report, rp, **kw):
        seen["rp"] = rp
        return "Scope: x\n(rows)"
    with mock.patch.object(server, "api", fake_api), mock.patch.object(server, "ALIASES", "/nonexistent/aliases.json"), \
         mock.patch.object(server, "_today", return_value=TODAY), mock.patch.object(server, "t_run_report", fake_report):
        server.call_tool("run_recipe", args)
    return seen["rp"]

# (label, args, key in report params, expected value)  -- collections: StartDate/EndDate/PropOwnerIDs; delinquency: IncludeProspects
P = lambda v: ("property " + repr(v), {"name": "collections", "params": {"property": v}}, "PropOwnerIDs", "(12)")
ROWS = [
    P(12), P("12"), P([12]), P("ID 12"), P("id: 12"), P("Oak Court"), P("OAK"), P([12, "Elm"]) [:2] + ("PropOwnerIDs", "(12,7)"),
    ("property all", {"name": "collections", "params": {"property": "all"}}, "PropOwnerIDs", "None"),
    ("params JSON string", {"name": "collections", "params": '{"property": "12"}'}, "PropOwnerIDs", "(12)"),
    ("nested params.params", {"name": "collections", "params": {"params": {"property": 12}}}, "PropOwnerIDs", "(12)"),
    ("top-level property", {"name": "collections", "property": 12}, "PropOwnerIDs", "(12)"),
    ("top-level period", {"name": "collections", "period": "2026-09"}, "StartDate", "2026-09-01"),
    ("name Rent Roll", {"name": "Rent Roll", "property": 12, "period": "2026-09"}, "StartDate", "2026-09-01"),
    ("name rent-roll", {"name": "rent-roll", "property": 12, "period": "2026-09"}, "StartDate", "2026-09-01"),
    ("name Collections ", {"name": " Collections ", "property": 12}, "PropOwnerIDs", "(12)"),
    ("bool yes", {"name": "delinquency", "include_prospects": "yes"}, "IncludeProspects", "True"),
    ("bool Y", {"name": "delinquency", "include_prospects": "Y"}, "IncludeProspects", "True"),
    ("bool no", {"name": "delinquency", "include_prospects": "no"}, "IncludeProspects", "False"),
    ("bool False", {"name": "delinquency", "include_prospects": False}, "IncludeProspects", "False"),
    ("bool true", {"name": "delinquency", "include_prospects": True}, "IncludeProspects", "True"),
    ("date 9/1/26", {"name": "collections", "start": "9/1/26", "end": "9/30/26"}, "StartDate", "2026-09-01"),
    ("date 9/30/26", {"name": "collections", "start": "9/1/26", "end": "9/30/26"}, "EndDate", "2026-09-30"),
    ("date 2026-9-1", {"name": "collections", "start": "2026-9-1", "end": "2026-9-30"}, "StartDate", "2026-09-01"),
    ("date Sept 30 2026", {"name": "collections", "start": "Sept 1 2026", "end": "Sept 30 2026"}, "EndDate", "2026-09-30"),
    ("date September 30, 2026", {"name": "collections", "start": "September 1, 2026", "end": "September 30, 2026"}, "EndDate", "2026-09-30"),
    ("date 2026/09/05", {"name": "collections", "start": "2026/09/05"}, "StartDate", "2026-09-05"),
    ("period September", {"name": "collections", "period": "September"}, "StartDate", "2026-09-01"),
    ("period September end", {"name": "collections", "period": "September"}, "EndDate", "2026-09-30"),
    ("period Sep 2026", {"name": "collections", "period": "Sep 2026"}, "StartDate", "2026-09-01"),
    ("period 2026-09", {"name": "collections", "period": "2026-09"}, "EndDate", "2026-09-30"),
    ("period last month", {"name": "collections", "period": "last month"}, "StartDate", "2026-09-01"),
    ("period last_month end", {"name": "collections", "period": "last_month"}, "EndDate", "2026-09-30"),
    ("period Q3 2026 start", {"name": "collections", "period": "Q3 2026"}, "StartDate", "2026-07-01"),
    ("period Q3 2026 end", {"name": "collections", "period": "Q3 2026"}, "EndDate", "2026-09-30"),
    ("period 2026 Q1", {"name": "collections", "period": "2026 Q1"}, "EndDate", "2026-03-31"),
    ("period last quarter start", {"name": "collections", "period": "last quarter"}, "StartDate", "2026-07-01"),
    ("period last quarter end", {"name": "collections", "period": "last quarter"}, "EndDate", "2026-09-30"),
    ("period this quarter", {"name": "collections", "period": "this quarter"}, "StartDate", "2026-10-01"),
    ("period 2025 start", {"name": "collections", "period": "2025"}, "StartDate", "2025-01-01"),
    ("period 2025 end", {"name": "collections", "period": "2025"}, "EndDate", "2025-12-31"),
    ("period last year start", {"name": "collections", "period": "last year"}, "StartDate", "2025-01-01"),
    ("period last year end", {"name": "collections", "period": "last year"}, "EndDate", "2025-12-31"),
    ("period this year end", {"name": "collections", "period": "this year"}, "StartDate", "2026-01-01"),
    ("start Q2 2026", {"name": "collections", "start": "Q2 2026", "end": "Q2 2026"}, "EndDate", "2026-06-30"),
    ("start 2025", {"name": "collections", "start": "2025", "end": "2025"}, "EndDate", "2025-12-31"),
    ("period ytd", {"name": "collections", "period": "ytd"}, "StartDate", "2026-01-01"),
]

# failing shapes: (label, args, text the error must contain)
ERRS = [
    ("bad recipe", {"name": "zzzqq"}, "run_recipe(name="),
    ("bad property", {"name": "collections", "property": "Nowhere Plaza"}, "e.g."),
    ("bad period", {"name": "collections", "period": "soonish"}, "e.g."),
    ("bad date", {"name": "collections", "start": "next whenever"}, "e.g."),
    ("exact-date period", {"name": "collections", "period": "2026-09-05"}, "e.g."),
    ("params not json", {"name": "collections", "params": "{not json"}, "e.g."),
    ("unknown param", {"name": "collections", "params": {"bogus": 1}}, "e.g."),
    ("bad basis", {"name": "profit_and_loss", "property": 12, "basis": "weird"}, "e.g."),
    ("bad number", {"name": "delinquency", "balance_over": "lots"}, "e.g."),
]

class Table(unittest.TestCase):
    def test_rows(self):
        bad = []
        for label, args, key, want in ROWS:
            try:
                got = run(dict(args)).get(key)
            except Exception as e:
                got = "EXC %s: %s" % (type(e).__name__, str(e)[:100])
            if str(got) != want: bad.append("%s: want %r got %r" % (label, want, got))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_errors_show_example(self):
        bad = []
        for label, args, needle in ERRS:
            try:
                run(dict(args)); bad.append("%s: no error" % label)
            except server.ToolError as e:
                if needle not in str(e): bad.append("%s: no %r in %r" % (label, needle, str(e)[:160]))
            except Exception as e:
                bad.append("%s: %s %s" % (label, type(e).__name__, e))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

if __name__ == "__main__":
    unittest.main()
