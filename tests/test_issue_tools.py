#!/usr/bin/env python3
"""Offline tests: issue-draft scrubber (tools/make_issue.py) and public-tree builder (tools/make_public_tree.py).
Run: python3 tests/test_issue_tools.py
"""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import make_issue as MI
import make_public_tree as MP

TERMS = ["acmeco", "jsmith-api", "Oakwood Heights"]


class Scrubber(unittest.TestCase):
    def test_text_scrub(self):
        s = ("Tenant John Smith (jane.doe@example.com, 555-123-4567) owes $1,234.56 at Oakwood Heights, unit 4021, "
             "user jsmith-api on acmeco" + ".api.rentmanager.com path C:" + chr(92) + "Users" + chr(92) + "bob" + chr(92) + "x")
        out = MI.scrub_text(s, TERMS)
        for bad in ("John", "Smith", "jane", "example.com", "555", "1,234", "Oakwood", "4021", "jsmith", "acmeco", "bob"):
            self.assertNotIn(bad, out, bad)

    def test_keeps_structure_words(self):
        out = MI.scrub_text("ERROR: Per-record fan-out budget reached; use run_report on Rent Manager", TERMS)
        self.assertIn("fan-out budget", out)
        self.assertIn("Rent Manager", out)

    def test_call_scrub_drops_values_keeps_names(self):
        rec = {"ts": "t", "tool": "query", "secs": 1.5, "isError": True,
               "args": {"resource": "Tenants", "filters": "LastName,eq,Smith;PropertyID,in,(12,13)", "term": "Oakwood Heights",
                        "params": {"property": "Oakwood Heights", "as_of": "2026-01-01"}, "fields": "Name,Balance"},
               "error": "No tenant named Jane Roe at 555-222-3333"}
        out = MI.scrub_call(rec, TERMS)
        blob = json.dumps(out)
        for bad in ("Smith", "12,13", "Oakwood", "2026-01-01", "Jane", "Roe", "555"):
            self.assertNotIn(bad, blob, bad)
        self.assertEqual(out["tool"], "query")
        self.assertEqual(out["args"]["resource"], "Tenants")
        self.assertEqual(out["args"]["fields"], "Name,Balance")
        self.assertIn("LastName,eq,<v>", out["args"]["filters"])
        self.assertTrue(out["isError"])

    def test_build_draft_from_log(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "calls.jsonl")
            rows = [{"tool": "resolve", "args": {"term": "Oakwood Heights"}, "secs": 0.2, "isError": False} for _ in range(3)]
            rows.append({"tool": "run_recipe", "args": {"name": "delinquency", "params": {"property": "Oakwood Heights"}},
                         "secs": 2.0, "isError": True, "error": "boom for jsmith-api"})
            open(log, "w", encoding="utf-8").write("\n".join(json.dumps(r) for r in rows) + "\n")
            body = MI.build(2, with_doctor=False, kind="bug", note="Jane Smith at Oakwood Heights failed", log=log, terms=TERMS)
        self.assertIn("run_recipe", body)
        self.assertIn("delinquency", body)
        for bad in ("Oakwood", "jsmith", "Smith"):
            self.assertNotIn(bad, body)
        self.assertEqual(body.count('"tool"'), 2)

    def test_missing_log_is_ok(self):
        body = MI.build(5, with_doctor=False, log=os.path.join(tempfile.gettempdir(), "no-such-log.jsonl"), terms=[])
        self.assertIn("no call log found", body)


class PublicTree(unittest.TestCase):
    def make_repo(self, d, extra_files=None):
        files = {"tools/publish_manifest.txt": "# c\nREADME.md\nrecipes/*.json\ntools/publish_manifest.txt\n",
                 "README.md": "hello\n", "recipes/a.json": "{}", "PROGRESS.md": "private\n", "CLAUDE.local.md": "private\n",
                 "local/x.txt": "private", "tests/test_hygiene.py": ""}
        files.update(extra_files or {})
        for rel, txt in files.items():
            p = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(txt)

    def test_only_manifest_files_copied(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_repo(d)
            out = os.path.join(d, "pub")
            res = MP.build(out, root=d, scan=False)
            self.assertEqual(res["files"], ["README.md", "recipes/a.json", "tools/publish_manifest.txt"])
            self.assertEqual(res["unmatched"], [])
            self.assertFalse(os.path.exists(os.path.join(out, "PROGRESS.md")))
            self.assertFalse(os.path.exists(os.path.join(out, "CLAUDE.local.md")))
            self.assertFalse(os.path.exists(os.path.join(out, "local")))

    def test_never_copies_secrets_even_if_listed(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_repo(d, {"tools/publish_manifest.txt": "README.md\n.env\nCLAUDE.local.md\n", ".env": "RM_PASSWORD=x\n"})
            res = MP.build(os.path.join(d, "pub"), root=d, scan=False)
            self.assertEqual(res["files"], ["README.md"])
            self.assertEqual(sorted(res["unmatched"]), [".env", "CLAUDE.local.md"])

    def test_refuses_non_empty_output(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_repo(d)
            os.makedirs(os.path.join(d, "pub")); open(os.path.join(d, "pub", "x"), "w").write("x")
            with self.assertRaises(ValueError): MP.build(os.path.join(d, "pub"), root=d, scan=False)

    def test_unmatched_pattern_reported(self):
        with tempfile.TemporaryDirectory() as d:
            self.make_repo(d, {"tools/publish_manifest.txt": "README.md\nnope/*.md\n"})
            res = MP.build(os.path.join(d, "pub"), root=d, scan=False)
            self.assertEqual(res["unmatched"], ["nope/*.md"])

    def test_real_manifest_is_consistent(self):
        """Every pattern in the real manifest matches something and none names a private dev file."""
        pats = MP.read_manifest(HERE)
        files, unmatched = MP.expand(pats, MP.disk_files(HERE))
        self.assertEqual(unmatched, [])
        for rel in files:
            base = os.path.basename(rel)
            self.assertNotIn(base, ("PROGRESS.md", "AUTOPILOT.md", "DECISIONS.md", "CLAUDE.local.md", ".env"))
            self.assertFalse(rel.startswith(("plans/", "local/", "out/", "spec/", ".claude/", "dev-notes/")), rel)
            self.assertFalse(base.startswith(("RELEASE-", "DRAFT-")), rel)


if __name__ == "__main__":
    unittest.main()
