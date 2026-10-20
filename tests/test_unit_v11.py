#!/usr/bin/env python3
"""Offline unit tests (v11): file downloads (allowlist, redirects, size cap, streaming), locations (thread-local), file_ref parsing and routing,
find_attachments / download_file with stubbed API replies, prune policy, one-to-many flattening, nested HistoryNotes filter routing,
404 hints, scope lines. Synthetic ids and names only; no network."""
import os, sys, io, json, time, tempfile, threading, unittest, urllib.request, urllib.error
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import rm, server

GOOD = "https://files.rentmanager.com/files/get/?EID=1&FKey=SECRETSIG"


class FakeResp:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status, self.headers, self.pos = data, status, headers or {"Content-Type": "application/pdf"}, 0
    def read(self, n=-1):
        n = len(self.data) if n is None or n < 0 else n
        b = self.data[self.pos:self.pos + n]; self.pos += len(b); return b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Allowlist(unittest.TestCase):
    def test_hosts(self):
        for ok in (GOOD, "https://rentmanager.com/x", "https://a.b.rentmanager.com:443/x"):
            rm.check_file_url(ok)
        for bad in ("http://files.rentmanager.com/x", "https://evil.com/x", "https://rentmanager.com.evil.com/x", "https://evilrentmanager.com/x",
                    "https://u:p@files.rentmanager.com/x", "https://files.rentmanager.com:8443/x", "ftp://files.rentmanager.com/x", "/relative", ""):
            with self.assertRaises(rm.RMError, msg=bad):
                rm.check_file_url(bad)

    def test_redirect_other_host_refused(self):
        h = rm._SafeRedirect()
        req = urllib.request.Request(GOOD)
        with self.assertRaises(rm.RMError):
            h.redirect_request(req, None, 302, "Found", {}, "https://evil.com/steal")
        with self.assertRaises(rm.RMError):
            h.redirect_request(req, None, 302, "Found", {}, "http://files.rentmanager.com/x")
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", {}, "https://other.rentmanager.com/y"))

    def test_no_token_header_sent(self):
        seen = {}
        def fake_open(req, timeout=None):
            seen.update(req.headers); seen["method"] = req.get_method(); return FakeResp(b"%PDF-1.4 x")
        with mock.patch.object(rm, "get_token", side_effect=AssertionError("token must not be fetched")):
            st, h, data, n, sha = rm.fetch_file(GOOD, _open=fake_open)
        self.assertEqual((st, data, n), (200, b"%PDF-1.4 x", 10))
        self.assertEqual(seen["method"], "GET")
        self.assertFalse([k for k in seen if "token" in k.lower() or "authorization" in k.lower()])

    def test_memory_cap(self):
        with self.assertRaises(rm.RMError):
            rm.fetch_file(GOOD, max_bytes=100, _open=lambda r, timeout=None: FakeResp(b"x" * 500))

    def test_stream_to_disk_and_cap_removes_partial(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "a.pdf")
            st, h, data, n, sha = rm.fetch_file(GOOD, dest=dest, _open=lambda r, timeout=None: FakeResp(b"y" * 200000))
            self.assertEqual((st, data, n), (200, None, 200000))
            self.assertEqual(os.path.getsize(dest), 200000)
            self.assertEqual(os.listdir(d), ["a.pdf"])
            dest2 = os.path.join(d, "b.pdf")
            with self.assertRaises(rm.RMError):
                rm.fetch_file(GOOD, dest=dest2, max_bytes=100000, _open=lambda r, timeout=None: FakeResp(b"y" * 200000))
            self.assertEqual(sorted(os.listdir(d)), ["a.pdf"])   # no partial, no .part

    def test_hard_limit_is_2gb(self):
        self.assertEqual(rm.MAX_FILE_BYTES, 2 * 1024 ** 3)

    def test_get_only_guard_still_there(self):
        with self.assertRaises(rm.RMError):
            rm._req("DELETE", "/Tenants/1")


class Location(unittest.TestCase):
    LOCS = [{"id": "1", "name": "Main DB", "friendly": "", "default": True}, {"id": "7", "name": "Second DB", "friendly": "2nd", "default": False}]

    def setUp(self):
        self._old = server._LOCS["data"]; server._LOCS["data"] = self.LOCS
        rm.set_location(None)
    def tearDown(self):
        server._LOCS["data"] = self._old; rm.set_location(None)

    def test_header_uses_thread_local_and_workers_inherit(self):
        seen = []
        def fake_req(method, path, headers=None, body=None, timeout=None, binary=False):
            seen.append(dict(headers or {}).get("x-rm12api-locationid")); return 200, {}, "[]"
        with mock.patch.object(rm, "_req", fake_req), mock.patch.object(rm, "get_token", return_value="t"), mock.patch.object(rm, "_throttle"):
            rm.set_location("7"); rm.get("/Units")
            with server.ThreadPoolExecutor(2) as ex: list(ex.map(lambda _: rm.get("/Units"), range(2)))
            rm.set_location(None); rm.get("/Units", loc="3")
        self.assertEqual(seen[:3], ["7", "7", "7"]); self.assertEqual(seen[3], "3")

    def test_call_tool_strips_sets_resets_and_tags_scope(self):
        tool = ("zz_probe", "d", {}, [], lambda: "rows: 1\nScope: All properties (location=%s)" % rm.current_location())
        with mock.patch.object(server, "TOOLS", server.TOOLS + [tool]):
            out = server.call_tool("zz_probe", {"location": "second db"})
            self.assertIn("(location=7)", out); self.assertIn("Scope: [Location Second DB, ID 7] All", out)
            self.assertIsNone(rm.current_location())
            out = server.call_tool("zz_probe", {"location": "Main DB"})   # default location: no override, no tag
            self.assertIn("(location=None)", out); self.assertNotIn("[Location", out)
            out = server.call_tool("zz_probe", {"location": 7})
            self.assertIn("[Location Second DB", out)

    def test_unknown_and_ambiguous_location(self):
        with self.assertRaises(server.ToolError) as c: server.resolve_location("nowhere")
        self.assertIn("Main DB (ID 1)", str(c.exception)); self.assertIn("separate database", str(c.exception))
        with self.assertRaises(server.ToolError): server.resolve_location("DB")

    def test_location_arg_on_every_tool(self):
        for t in server.tool_list():
            if t["name"] not in server.NO_LOCATION: self.assertIn("location", t["inputSchema"]["properties"], t["name"])

    def test_tag_without_scope_line(self):
        self.assertIn("Scope: [Location Second DB, ID 7]", server.tag_location("hello", self.LOCS[1]))

    def test_property_index_not_shared_across_locations(self):
        rm.set_location("7")
        with mock.patch.object(server, "_live_properties", return_value=[{"id": 5, "name": "Other"}]) as lp:
            self.assertEqual(server.properties()[0]["name"], "Other"); server.properties()
            self.assertEqual(lp.call_count, 1)
        server._LOC_PROPS.clear()


class Refs(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(server.parse_ref("history:12:34")[:4], ("history", 12, 34, "/History/12/Attachments"))
        k = server.parse_ref("leaserenewal:5:6"); self.assertEqual(k[3], "/LeaseRenewals"); self.assertEqual(k[4]["filters"], "LeaseRenewalID,eq,5")
        self.assertEqual(server.parse_ref("propertyfile:3:9")[3], "/Properties/3/PropertyFiles")
        k = server.parse_ref("udf_tenant:8:2"); self.assertEqual(k[3], "/Tenants/8/UserDefinedValues"); self.assertIn("Attachment.FileID,eq,2", k[4]["filters"])
        self.assertEqual(server.parse_ref("udf_service_issue:8:2")[3], "/ServiceManagerIssues/8/UserDefinedValues")
        for bad in ("", "x", "history:1", "history:a:b", "nope:1:2", "udf_zzz:1:2", "../history:1:2"):
            with self.assertRaises(server.ToolError, msg=bad): server.parse_ref(bad)

    def test_find_file_nested(self):
        d = [{"LeaseRenewalAttachments": [{"File": {"FileID": 6, "DownloadURL": "u6"}}, {"File": {"FileID": 7, "DownloadURL": "u7"}}]}]
        self.assertEqual(server._find_file(d, 7)["DownloadURL"], "u7"); self.assertIsNone(server._find_file(d, 8))

    def test_ids(self):
        self.assertEqual(server.parse_ids("1, 2;3"), [1, 2, 3]); self.assertEqual(server.parse_ids(5), [5]); self.assertEqual(server.parse_ids([1, "2", 1]), [1, 2])
        with self.assertRaises(server.ToolError): server.parse_ids([])
        with self.assertRaises(server.ToolError): server.parse_ids(list(range(server.FANOUT_BUDGET + 1)))
        with self.assertRaises(server.ToolError): server.parse_ids(["a"])


def hist_note(hid, fid, name, ext=".pdf", date="2026-03-01T00:00:00", cat="Lease", note="n", extra=None):
    n = {"HistoryID": hid, "HistoryDate": date, "Note": note, "FileID": fid, "HistoryCategory": {"Name": cat},
         "Attachment": {"FileID": fid, "Name": name, "Extension": ext, "Description": "", "CreateDate": date},
         "HistoryAttachments": [{"FileID": fid, "File": {"FileID": fid, "Name": name}}]}
    if extra: n["HistoryAttachments"].append({"FileID": extra, "File": {"FileID": extra, "Name": "extra", "Extension": ".png", "CreateDate": date}})
    return n


class Find(unittest.TestCase):
    def test_history_rows_sorted_no_urls_no_truncation(self):
        long = "A" * 150
        calls = []
        def fake_fetch(path, params, max_records=500, **k):
            calls.append((path, params));
            return ([hist_note(1, 10, "old", date="2025-01-01T00:00:00"), hist_note(2, 11, long, date="2026-05-01T00:00:00", extra=12)], 2, 1, 200),
        def fetch2(path, params, max_records=500, **k): return fake_fetch(path, params)[0]
        with mock.patch.object(server, "fetch", fetch2), mock.patch.object(server, "hist_categories", return_value={3: "Lease", 6: "Rider"}):
            out = server.t_find_attachments("tenant_history", [41], category="lease", start="2025-01-01")
        path, params = calls[0]
        self.assertEqual(path, "/Tenants/41/HistoryNotes")
        self.assertIn("FileID,gt,0", params["filters"]); self.assertIn("HistoryCategoryID,in,(3)", params["filters"]); self.assertIn("HistoryDate,ge,2025-01-01", params["filters"])
        lines = [l for l in out.split("\n") if l.startswith("history:")]
        self.assertEqual([l.split("|")[0] for l in lines], ["history:2:11", "history:2:12", "history:1:10"])   # newest first, extra file listed
        self.assertIn(long, out)                              # cells are never cut
        self.assertNotIn("DownloadURL", out); self.assertNotIn("http", out)
        self.assertIn("file_ref", out.split("\n")[1] + out.split("\n")[0])

    def test_filters_local(self):
        with mock.patch.object(server, "fetch", lambda *a, **k: ([hist_note(1, 10, "Lease A"), hist_note(2, 11, "Photo", ext=".jpg")], 2, 1, 200)):
            o = server.t_find_attachments("unit_history", 5, extension="pdf")
            self.assertIn("history:1:10", o); self.assertNotIn("history:2:11", o)
            o = server.t_find_attachments("unit_history", 5, name_contains="photo")
            self.assertIn("history:2:11", o); self.assertNotIn("history:1:10", o)

    def test_routes_per_source(self):
        seen = []
        def fetch2(path, params, max_records=500, **k): seen.append(path); return [], 0, 1, 204
        with mock.patch.object(server, "fetch", fetch2):
            for src, ctrl in server.HIST_SOURCES.items(): server.t_find_attachments(src, 9)
            server.t_find_attachments("property_files", 9); server.t_find_attachments("custom_field", 9, parent="unit"); server.t_find_attachments("lease", [1, 2])
        self.assertEqual(seen[:6], ["/%s/9/HistoryNotes" % c for c in server.HIST_SOURCES.values()])
        self.assertEqual(seen[6:], ["/Properties/9/PropertyFiles", "/Units/9/UserDefinedValues", "/LeaseRenewals"])

    def test_lease_batches_and_rows(self):
        params_seen = []
        lr = [{"LeaseRenewalID": 5, "ParentLeaseID": 1, "UnitID": 8, "StartDate": "2025-01-01T00:00:00", "EndDate": "2025-12-31T00:00:00",
               "LeaseRenewalAttachments": [{"LeaseAttachmentType": "Lease", "FileID": 70, "File": {"FileID": 70, "Name": "x", "Extension": ".pdf", "DownloadURL": "https://s/SECRET", "CreateDate": "2025-01-02T00:00:00"}}]},
              {"LeaseRenewalID": 6, "ParentLeaseID": 1, "UnitID": 8, "StartDate": "2026-01-01T00:00:00", "EndDate": "2026-12-31T00:00:00", "LeaseRenewalAttachments": []}]
        def fetch2(path, params, max_records=500, **k): params_seen.append(params); return lr, 2, 1, 200
        with mock.patch.object(server, "fetch", fetch2):
            out = server.t_find_attachments("lease", list(range(1, 41)))
        self.assertEqual(params_seen[0]["filters"].split(",in,")[0], "ParentLeaseID")
        self.assertIn("leaserenewal:5:70", out); self.assertIn("1 without any attachment", out); self.assertNotIn("SECRET", out)
        self.assertIn("term 2025-01-01 to 2025-12-31", out)

    def test_bad_source_and_all_ids_failing(self):
        with self.assertRaises(server.ToolError): server.t_find_attachments("nope", 1)
        with mock.patch.object(server, "fetch", side_effect=server.ToolError("HTTP 500")):
            with self.assertRaises(server.ToolError): server.t_find_attachments("tenant_history", [1, 2])

    def test_category_names(self):
        with mock.patch.object(server, "hist_categories", return_value={3: "Lease", 36: "Lease Termination", 6: "Rider"}):
            self.assertEqual(server.category_ids("Lease"), [3])
            self.assertEqual(server.category_ids("lease termination,6"), [36, 6])
            self.assertEqual(server.category_ids("term"), [36])
            with self.assertRaises(server.ToolError): server.category_ids("zzz")


class Download(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rm_v11_")
        self._env = mock.patch.dict(os.environ, {"RM_OUT": self.tmp}); self._env.start()
    def tearDown(self):
        self._env.stop()

    FILE = {"FileID": 99, "Name": "../../evil name:?.pdf", "Extension": ".pdf", "DownloadURL": GOOD}

    def _resolve(self, f=None):
        return mock.patch.object(server, "resolve_ref", return_value=f or self.FILE)

    def test_link_mode_downloads_nothing(self):
        with self._resolve(), mock.patch.object(rm, "fetch_file", side_effect=AssertionError("no download in link mode")):
            out = server.t_download_file("history:1:99", mode="link")
        self.assertIn(GOOD, out); self.assertIn("like a password", out); self.assertFalse(os.path.exists(os.path.join(self.tmp, "files")))

    def test_text_mode_in_memory(self):
        f = {"FileID": 5, "Name": "n", "Extension": ".txt", "DownloadURL": GOOD}
        big = ("line\n" * 10000).encode()
        with self._resolve(f), mock.patch.object(rm, "fetch_file", return_value=(200, {"content-type": "text/plain"}, big, len(big), "ab")) as ff:
            out = server.t_download_file("history:1:5")   # auto: text-like -> text
        self.assertNotIn("dest", ff.call_args.kwargs)
        self.assertIn("first %d of %d characters" % (server.INLINE_CAP, len(big)), out); self.assertIn("saved: nothing", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "files")))
        self.assertNotIn("SECRETSIG", out)

    def test_auto_saves_binary_with_safe_name_and_file_line(self):
        def fake(url, dest=None, **k):
            open(dest, "wb").write(b"%PDF-1.4 hello"); return 200, {"content-type": "application/pdf"}, None, 14, "cafe"
        with self._resolve(), mock.patch.object(rm, "fetch_file", side_effect=fake), mock.patch.object(server, "pdf_text", return_value=("", "no text found (scan)")):
            out = server.t_download_file("history:1:99")
        lines = out.split("\n")
        fl = [l for l in lines if l.startswith("File: ")]
        self.assertEqual(len(fl), 1); path = fl[0][6:]
        self.assertTrue(os.path.isabs(path)); self.assertEqual(os.path.dirname(path), os.path.join(self.tmp, "files"))
        self.assertTrue(path.endswith(".pdf")); self.assertNotIn("..", os.path.basename(path)); self.assertNotIn(":", os.path.basename(path))
        self.assertEqual(open(path, "rb").read(), b"%PDF-1.4 hello")
        self.assertEqual(lines[lines.index(fl[0]) + 1], server.FILE_HINT)           # reminder right after the File line
        for word in ("http", "FKey", "SECRETSIG", "EID="): self.assertNotIn(word, out + server.FILE_HINT)
        self.assertIn("sha256 cafe", out); self.assertIn("scan", out)
        with self._resolve(), mock.patch.object(rm, "fetch_file", side_effect=fake), mock.patch.object(server, "pdf_text", return_value=("", "")):
            out2 = server.t_download_file("history:1:99")   # second save never overwrites
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, "files"))), 2)

    def test_failed_download_leaves_no_file(self):
        with self._resolve(), mock.patch.object(rm, "fetch_file", side_effect=rm.RMError("file is larger than the 2048 MB limit")):
            with self.assertRaises(server.ToolError) as c: server.t_download_file("history:1:99", mode="save")
        self.assertIn("limit", str(c.exception)); self.assertEqual(os.listdir(os.path.join(self.tmp, "files")), [])
        with self._resolve(), mock.patch.object(rm, "fetch_file", return_value=(403, {}, b"", 0, None)):
            with self.assertRaises(server.ToolError): server.t_download_file("history:1:99", mode="save")
        self.assertEqual(os.listdir(os.path.join(self.tmp, "files")), [])

    def test_raw_url_checked(self):
        with self.assertRaises(server.ToolError): server.t_download_file(download_url="https://evil.com/x")
        with self.assertRaises(server.ToolError): server.t_download_file()

    def test_batch_cap_and_lines(self):
        with self.assertRaises(server.ToolError): server.t_download_file(["history:1:%d" % i for i in range(21)])
        f = {"FileID": 5, "Name": "n", "Extension": ".txt", "DownloadURL": GOOD}
        def res(ref):
            if ref == "bad": raise server.ToolError("Bad file_ref 'bad'")
            return f
        with mock.patch.object(server, "resolve_ref", res), mock.patch.object(rm, "fetch_file", return_value=(200, {"content-type": "text/plain"}, b"hi", 2, "ab")):
            out = server.t_download_file(["history:1:5", "history:2:5", "bad"])
        self.assertIn("history:1:5 ->", out); self.assertIn("bad -> FAILED", out)

    def test_pdf_text_without_library_says_so(self):
        with mock.patch.dict(sys.modules, {"pdfplumber": None, "pypdf": None}):
            t, note = server.pdf_text(b"%PDF-1.4")
        self.assertEqual(t, ""); self.assertIn("no PDF text library", note)

    def test_safe_name(self):
        for bad in ("../../x", "..\\..\\x.pdf", "C:\\a\\b.pdf", "CON", "a/b/c.pdf", "", "...."):
            n = server.safe_name(bad)
            self.assertTrue(n and "/" not in n and "\\" not in n and ".." not in n and ":" not in n, (bad, n))

    def test_report_pdf_has_hint_once(self):
        self.assertEqual(server.file_lines("a", "b").count(server.FILE_HINT), 1)
        self.assertEqual(server.file_lines(None), "")


class Prune(unittest.TestCase):
    def test_policy(self):
        with tempfile.TemporaryDirectory() as top:
            os.makedirs(os.path.join(top, "files")); os.makedirs(os.path.join(top, "sub"))
            old = time.time() - 20 * 86400; new = time.time() - 3 * 86400
            def mk(rel, mt):
                p = os.path.join(top, rel)
                with open(p, "w") as fh: fh.write("x")
                os.utime(p, (mt, mt)); return p
            gone = [mk("Units_20260101-101010.json", old), mk("RentRoll_x_20260101-101010-2.pdf", old), mk("files/any name.pdf", old)]
            kept = [mk("Units_20260901-101010.json", new), mk("notes.txt", old), mk("keep_me.csv", old), mk("sub/Units_20260101-101010.json", old), mk("files/fresh.pdf", new)]
            self.assertEqual(server.prune_downloads(days=14, base=top), 3)
            self.assertFalse(any(os.path.exists(p) for p in gone)); self.assertTrue(all(os.path.exists(p) for p in kept))
            for p in kept: os.utime(p, (old, old)) if p.endswith("fresh.pdf") else None
            self.assertEqual(server.prune_downloads(days=0, base=top), 0)   # 0 = keep forever
            self.assertTrue(os.path.exists(kept[-1]))

    def test_default_is_14_days(self):
        with tempfile.TemporaryDirectory() as top, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RM_OUT_KEEP_DAYS", None)
            os.makedirs(os.path.join(top, "files"))
            a = os.path.join(top, "files", "a.pdf"); b = os.path.join(top, "files", "b.pdf")
            for p, days in ((a, 10), (b, 15)):
                with open(p, "w") as fh: fh.write("x")
                t = time.time() - days * 86400; os.utime(p, (t, t))
            self.assertEqual(server.prune_downloads(base=top), 1); self.assertTrue(os.path.exists(a)); self.assertFalse(os.path.exists(b))

    def test_once_per_day(self):
        server._PRUNE["t"] = -1e9
        with mock.patch.object(server, "prune_downloads", return_value=2) as p:
            server.maybe_prune(now=1000.0); server.maybe_prune(now=1000.0 + 3600); server.maybe_prune(now=1000.0 + 90000)
        self.assertEqual(p.call_count, 2)
        server._PRUNE["t"] = time.time()


class QueryFixes(unittest.TestCase):
    def test_explode_one_to_many(self):
        row = {"LeaseID": 1, "Tenant": {"Name": "T"}, "LeaseRenewals": [
            {"LeaseRenewalID": 5, "LeaseRenewalAttachments": [{"File": {"Name": "a"}}, {"File": {"Name": "b"}}]},
            {"LeaseRenewalID": 6, "LeaseRenewalAttachments": []}], "Tags": ["x", "y"], "Other": [{"k": 1}, {"k": 2}]}
        rows = server.explode(row)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.get("LeaseRenewals.LeaseRenewalAttachments.File.Name") for r in rows], ["a", "b", None])
        self.assertTrue(all(r["LeaseID"] == 1 and r["Tenant.Name"] == "T" and r["Tags"] == "x,y" for r in rows))
        self.assertTrue(all(r["Other"] == "k=1 ; k=2" for r in rows))        # second unrelated list: readable text, not rows, not JSON
        self.assertEqual({r["LeaseRenewals.LeaseRenewalID"] for r in rows}, {5, 6})
        self.assertFalse([v for r in rows for v in r.values() if isinstance(v, str) and v.startswith("[{")])
        chain = server.explode({"id": 1, "R": [{"rid": 5, "A": [{"f": 1}, {"f": 2}]}, {"rid": 6, "A": [{"f": 3}]}, {"rid": 7, "A": []}]})
        self.assertEqual([(r["R.rid"], r.get("R.A.f")) for r in chain], [(5, 1), (5, 2), (6, 3), (7, None)])   # nested lists explode for EVERY parent child
        self.assertEqual(server.explode({"a": 1, "L": []}), [{"a": 1, "L": ""}])
        self.assertEqual(server.explode({"a": 1, "L": [{"b": 2}]}), [{"a": 1, "L.b": 2}])

    def test_route_history_filters(self):
        self.assertEqual(server.route_history_filters("Tenant.TenantID,in,(3,4);HistoryCategoryID,in,(3,6)"), ("Tenants", [3, 4], "HistoryCategoryID,in,(3,6)"))
        self.assertEqual(server.route_history_filters("Unit.UnitID,eq,9"), ("Units", [9], ""))
        self.assertIsNone(server.route_history_filters("HistoryCategoryID,eq,3"))
        with self.assertRaises(server.ToolError): server.route_history_filters("Tenant.TenantID,ne,3")

    def test_query_routes_to_subresource(self):
        calls = []
        with mock.patch.object(server, "_query_one", lambda res, flt="", **k: calls.append((res, flt, k.get("save_as"))) or "ok:" + res):
            out = server.t_query("HistoryNotes", "Tenant.TenantID,in,(3,4);HistoryCategoryID,in,(3)", fields="HistoryID")
        self.assertEqual([c[:2] for c in calls], [("Tenants/3/HistoryNotes", "HistoryCategoryID,in,(3)"), ("Tenants/4/HistoryNotes", "HistoryCategoryID,in,(3)")])
        self.assertIn("Routed", out)
        with mock.patch.object(server, "_query_one", lambda res, flt="", **k: "plain"):
            self.assertEqual(server.t_query("Units", "PropertyID,eq,1"), "plain")
            self.assertEqual(server.t_query("HistoryNotes", "HistoryCategoryID,eq,1"), "plain")
        with self.assertRaises(server.ToolError):
            server.t_query("HistoryNotes", "Tenant.TenantID,in,(%s)" % ",".join(str(i) for i in range(1, 40)))

    def test_nested_filter_not_advertised_and_refused(self):
        d = server.t_describe_model("HistoryNotes", "filters")
        self.assertNotIn("Tenant.TenantID", d); self.assertIn("nested filters", d); self.assertIn("top-level", d)
        _, _, _, _, probs = server.validate("HistoryNote", filters="Tenant.TenantID,eq,3")
        self.assertTrue(probs and "Tenants/{id}/HistoryNotes" in probs[0])

    def test_describe_filters_top_level_default_and_contains_lists_nested(self):
        d = server.t_describe_model("Leases", "filters")
        self.assertIn("hidden", d)
        d2 = server.t_describe_model("Leases", "filters", contains="Property.")
        self.assertIn("Property.", d2)

    def test_diagnose_key_error(self):
        sent = []
        def fake_api(path, params=None, timeout=None):
            sent.append(dict(params)); return (404, {}, {}) if params.get("embeds") else (200, {}, [])
        with mock.patch.object(server, "api", fake_api):
            msg = server.diagnose_key_error("/Leases", {"embeds": "Bogus", "filters": "UnitID,eq,1", "fields": "LeaseID"})
        self.assertIn("without embeds works", msg); self.assertIn("Bogus", msg); self.assertLessEqual(len(sent), 4)
        with mock.patch.object(server, "api", lambda *a, **k: (404, {}, {})):
            self.assertIn("Could not tell", server.diagnose_key_error("/X", {"filters": "A,eq,1"}))

    def test_fetch_ordered_adds_hint(self):
        def boom(path, params, *a, **k): raise server.ToolError("HTTP 404: The given key was not present in the dictionary.")
        with mock.patch.object(server, "fetch", boom), mock.patch.object(server, "api", lambda *a, **k: (200, {}, [])):
            with self.assertRaises(server.ToolError) as c: server.fetch_ordered("/Leases", {"embeds": "Bogus"})
        self.assertIn("likely cause", str(c.exception))

    def test_scope_from_filters_names_id_filters(self):
        s = server.scope_from_filters("UnitID,in,(1,2,3)")
        self.assertIn("UnitID", s); self.assertNotIn("All properties", s)
        self.assertIn("TenantID", server.scope_from_filters("TenantID,eq,5")); self.assertIn("LeaseID", server.scope_from_filters("LeaseID,in,(5,6)"))
        self.assertIsNone(server.scope_from_filters("IsActive,eq,true"))

    def test_leases_default_fields_include_dates(self):
        got = {}
        def fake(path, params, *a, **k):
            got.update(params); return [{"LeaseID": 1, "StartDate": "2025-01-01T00:00:00"}], 1, 1, 200
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RM_OUT": d}), mock.patch.object(server, "fetch_ordered", fake):
            out = server.t_query("Leases", "UnitID,in,(1,2)")
        for f in ("StartDate", "EndDate", "TenantID", "UnitID"): self.assertIn(f, got["fields"])
        self.assertIn("filtered by UnitID", out); self.assertNotIn("All properties", out)
        self.assertIn("File: ", out)

    def test_instruction_caps_and_tools_listed(self):
        self.assertLessEqual(len(server.INSTRUCTIONS), 1500)
        for n in ("find_attachments", "download_file", "locations"): self.assertIn(n, server.INSTRUCTIONS)
        for t in server.tool_list(): self.assertLessEqual(len(t["description"]), 1900, t["name"])
        self.assertRegex(server.VERSION, r"^\d+\.\d+\.\d+(-beta)?$")


class LeaseAudit(unittest.TestCase):
    def test_pointer_routes_from_find_recipe_and_search(self):
        for q in ("lease audit", "audit rent against lease", "lease vs rent roll"):
            lines = server.concept_lines(q)
            self.assertTrue(any("RENTAL-ASSISTANCE" in l.upper() and "find_attachments" in l and "summary='rent'" in l for l in lines), q)
        self.assertIn("rental-assistance", server.t_find_recipe("lease audit").lower())
        self.assertIn("lease audit", server.t_search_models("lease audit").lower())

    def test_note_on_rent_roll_outputs(self):
        self.assertIn("include rental assistance (RA) charges as rent", server.audit_note_for("SummaryRentRoll"))
        self.assertIn("join to Leases", server.audit_note_for("rent_roll"))
        self.assertEqual(server.audit_note_for("Delinquency"), "")
        with mock.patch.object(server, "run_recipe_result", return_value={"text": "body"}):
            self.assertIn("sum the rows per unit", server.t_run_recipe("rent_roll"))
            self.assertNotIn("Lease audit note", server.t_run_recipe("delinquency"))


if __name__ == "__main__":
    unittest.main()
