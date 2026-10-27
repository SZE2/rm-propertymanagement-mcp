#!/usr/bin/env python3
"""Offline tests for report_pdf (stubbed PDF bytes, no network)."""
import os, sys, json, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server
from offline_fixture import setUpModule

PDF = b"%PDF-1.4\n%fake\n"
PROPS = [{"id": 11, "name": "Oak Court", "short": "", "active": True, "aliases": []},
         {"id": 12, "name": "Elm Park", "short": "", "active": True, "aliases": []}]

class Stub:
    def __init__(self, mode="stream"): self.calls, self.mode = [], mode
    def __call__(self, path, params=None, timeout=None):
        self.calls.append((path, dict(params or {})))
        go = (params or {}).get("getOptions")
        if self.mode == "stream" and go == "ReturnPDFStream": return 200, {}, PDF
        if self.mode == "url":
            if go == "ReturnPDFStream": return 200, {}, b'{"x":1}'
            if go == "ReturnPDFUrl": return 200, {}, json.dumps("%s/Reports/DownloadFile?reportKey=abc" % server.rm.BASE).encode()
            if path.startswith("/Reports/DownloadFile"): return 200, {}, PDF
        if self.mode == "nodata": return 204, {}, b""
        return 500, {}, b'{"Message":"nope"}'

def run(stub, **kw):
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RM_OUT": d}), \
         mock.patch.object(server, "api_bytes", stub), mock.patch.object(server, "properties", lambda live=False: PROPS):
        text = server.t_report_pdf(**kw)
        files = sorted(os.listdir(d))
        data = [open(os.path.join(d, f), "rb").read() for f in files]
    return text, files, data

class ReportPdf(unittest.TestCase):
    def test_two_properties_stream(self):
        st = Stub()
        text, files, data = run(st, report="67", properties=["Oak Court", "12"])
        self.assertEqual(len(files), 2); self.assertTrue(all(d == PDF for d in data))
        self.assertTrue(all(f.startswith("RentRoll_") and f.endswith(".pdf") for f in files), files)
        self.assertIn("2 of 2 saved", text); self.assertIn("Scope: Property:", text)
        keys = sorted(c[1]["parameters"] for c in st.calls)
        self.assertEqual(keys, ["PropOwnerIDs,(11)", "PropOwnerIDs,(12)"])
        self.assertTrue(all(c[1]["getOptions"] == "ReturnPDFStream" for c in st.calls))

    def test_comma_string_and_name_lookup(self):
        text, files, _ = run(Stub(), report="RentRoll", properties="Oak Court, Elm Park")
        self.assertEqual(len(files), 2)

    def test_portfolio_single_pdf(self):
        st = Stub()
        text, files, _ = run(st, report=67)
        self.assertEqual(len(files), 1); self.assertIn("all_", files[0]); self.assertIn("Scope: All", text)
        self.assertNotIn("PropOwnerIDs", st.calls[0][1].get("parameters", ""))

    def test_url_fallback(self):
        text, files, data = run(Stub("url"), report="67", properties="Oak Court")
        self.assertEqual(data, [PDF]); self.assertIn("1 of 1 saved", text)

    def test_failure_text(self):
        text, files, _ = run(Stub("bad"), report="67", properties="Oak Court")
        self.assertEqual(files, []); self.assertIn("FAILED", text); self.assertIn("may not support PDF", text)
        self.assertIn("run_report", text)

    def test_no_data(self):
        text, files, _ = run(Stub("nodata"), report="67", properties="Oak Court")
        self.assertEqual(files, []); self.assertIn("HTTP 204", text)

    def test_registered_and_budget(self):
        names = [t["name"] for t in server.tool_list()]
        self.assertIn("report_pdf", names)
        d = next(t for t in server.tool_list() if t["name"] == "report_pdf")["description"]
        self.assertIn("Rent Manager", d); self.assertIn("PDF", d)
        self.assertLessEqual(max(len(t["description"]) for t in server.tool_list()), 1900)
        self.assertLessEqual(len(server.INSTRUCTIONS), 1500)

    def test_find_recipe_pdf_hint(self):
        t = server.t_find_recipe("pdf rent roll")
        self.assertIn("report_pdf(report=67", t)

if __name__ == "__main__":
    unittest.main()
