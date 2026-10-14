#!/usr/bin/env python3
"""Offline hygiene check: public files must not contain company-specific or personal data.
Scans for the RM_COMPANY value, real API hosts, property names from local/aliases.json, money figures,
home-directory paths, and personal/company strings listed in the UNTRACKED file local/blocklist.txt
(one term per line, case-insensitive; the license holder name is allowed only in LICENSE).
In a git checkout only files that are tracked AND listed in tools/publish_manifest.txt are scanned (the others are
private dev logs that never ship). In a folder without git (the public tree built by tools/make_public_tree.py)
every file is scanned. Skips checks whose source (.env / local/aliases.json / blocklist / git) is not available.
Run: python3 tests/test_hygiene.py [--root DIR]   (--root scans another folder, e.g. a public tree)
"""
import fnmatch, json, os, re, subprocess, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE
if "--root" in sys.argv:
    i = sys.argv.index("--root"); ROOT = os.path.abspath(sys.argv[i + 1]); del sys.argv[i:i + 2]
COMMON = {"between", "court", "place", "plaza", "tower", "towers", "apartments", "apartment", "village", "terrace", "gardens",
          "garden", "park", "house", "street", "avenue", "main", "north", "south", "east", "west", "center", "square"}
HOLDER = "full" + "hand"   # license holder name: the one blocklist term allowed in LICENSE
SKIP_DIRS = {".git", "__pycache__", "local", "out", "spec", ".claude", "dev-notes"}
PRIVATE_LOCAL = os.path.join(HERE, "local")   # blocklist/aliases/.env always come from the working repo


def git_tracked(root=ROOT):
    if not os.path.isdir(os.path.join(root, ".git")): return None
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\n")
    except (OSError, subprocess.CalledProcessError):
        return None
    return [x for x in out if x]


def walk_files(root):
    res = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            res.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    return res


def manifest_patterns(root=ROOT):
    try:
        lines = open(os.path.join(root, "tools", "publish_manifest.txt"), encoding="utf-8").read().split("\n")
    except OSError:
        return None
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def in_manifest(rel, pats):
    return any(fnmatch.fnmatch(rel, p) for p in pats)


def tracked(root=ROOT):
    """Files that would be public: git-tracked and in the manifest; or every file when there is no git checkout."""
    listed = git_tracked(root)
    if listed is None:
        rels = walk_files(root)
    else:
        pats = manifest_patterns(root)
        rels = [r for r in listed if pats is None or in_manifest(r, pats)]
    files = {}
    for rel in rels:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f: files[rel] = f.read()
        except (OSError, UnicodeDecodeError):
            pass
    return files


def env_company():
    try:
        for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
            if line.strip().startswith("RM_COMPANY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def property_terms():
    try:
        props = json.load(open(os.path.join(PRIVATE_LOCAL, "aliases.json"), encoding="utf-8"))["properties"]
    except (OSError, ValueError, KeyError):
        return []
    terms = set()
    for p in props:
        for n in [p.get("name"), p.get("short")] + list(p.get("aliases") or []):
            n = (n or "").strip()
            if len(n) >= 5 and n.lower() not in COMMON:
                terms.add(n)
    return sorted(terms)


# Home-directory paths. Placeholders (<name>, %VAR%, $VAR, ~) are fine. Built from pieces so this file does not match itself.
_U, _H = "Users", "home"
HOME_PATH_RX = (r"(?<![A-Za-z])[A-Za-z]:[\\/]+" + _U + r"[\\/]+(?![<%$\{])[A-Za-z0-9_.-]+"
                r"|(?<![A-Za-z0-9~.])/" + _U + r"/(?![<%$\{])[A-Za-z0-9_.-]+"
                r"|(?<![A-Za-z0-9~.])/" + _H + r"/(?![<%$\{])[A-Za-z0-9_.-]+")


def hits(files, pattern, flags=0):
    rx = re.compile(pattern, flags)
    return ["%s: %s" % (rel, rx.search(txt).group(0)) for rel, txt in files.items() if rx.search(txt)]


def home_path_hits(files):
    return hits(files, HOME_PATH_RX)


def blocklist_terms():
    try:
        lines = open(os.path.join(PRIVATE_LOCAL, "blocklist.txt"), encoding="utf-8").read().split("\n")
    except OSError:
        return []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def blocklist_hits(files, terms):
    found = []
    for n, t in enumerate(terms, 1):
        rx = re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(t), re.I)
        for rel, txt in files.items():
            if t.lower() == HOLDER and rel == "LICENSE": continue
            if rx.search(txt): found.append("%s: blocklist term #%d" % (rel, n))   # never echo the term itself
    return found


class Hygiene(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tracked()

    def need_git(self):
        if not self.files: self.skipTest("no files to scan")

    def test_company_code_absent(self):
        self.need_git()
        c = env_company()
        if not c: self.skipTest("no RM_COMPANY in .env")
        self.assertEqual(hits(self.files, r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(c), re.I), [])

    def test_api_host_generic(self):
        self.need_git()
        bad = [h for h in hits(self.files, r"(?<![%A-Za-z0-9-])[A-Za-z0-9-]+\.api\.rentmanager\.com")
               if not re.search(r"(<company>|%s|company)\.api" % "|".join(["example", "your-company"]), h, re.I)]
        self.assertEqual(bad, [])

    def test_property_names_absent(self):
        self.need_git()
        terms = property_terms()
        if not terms: self.skipTest("no local/aliases.json")
        found = []
        for t in terms:
            found += hits(self.files, r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(t), re.I)
        self.assertEqual(found, [])

    def test_no_real_money_figures(self):
        """Public files must not hold real-looking dollar totals (>= $10,000 with cents, or $NNk/$N.NM). Mark synthetic lines with 'hygiene: synthetic'."""
        self.need_git()
        rx = re.compile(r"\$\s?(?:[0-9]{2,3}(?:,[0-9]{3})+(?:\.[0-9]{2})|[0-9]{5,}\.[0-9]{2}|[0-9]{2,}(?:\.[0-9]+)?[kKM])")
        found = []
        for rel, txt in self.files.items():
            if rel == "tests/test_hygiene.py": continue
            for i, line in enumerate(txt.split(chr(10)), 1):
                if "hygiene: synthetic" in line: continue
                m = rx.search(line)
                if m: found.append("%s:%d: %s" % (rel, i, m.group(0)))
        self.assertEqual(found, [])

    def test_no_home_paths(self):
        self.need_git()
        self.assertEqual(home_path_hits(self.files), [])

    def test_home_path_detector_catches_plants(self):
        bs = chr(92)
        for txt in ("C:" + bs + _U + bs + "someone" + bs + "x", "C:/" + _U + "/someone/x", "/" + _U + "/someone/x", "/" + _H + "/someone/x"):
            self.assertTrue(home_path_hits({"f.txt": "see " + txt}), txt)
        for ok in ("C:" + bs + _U + bs + "<name>" + bs + "x", "~/Library/Application Support/Claude", "%APPDATA%" + bs + "Claude",
                   "/" + _U + "/<name>/x", "$HOME/x"):
            self.assertEqual(home_path_hits({"f.txt": ok}), [], ok)

    def test_blocklist_terms_absent(self):
        self.need_git()
        terms = blocklist_terms()
        if not terms: self.skipTest("no local/blocklist.txt")
        self.assertEqual(blocklist_hits(self.files, terms), [])

    def test_blocklist_detector_catches_plants(self):
        self.assertTrue(blocklist_hits({"a.md": "Contact Zork Inc today"}, ["zork inc"]))
        self.assertEqual(blocklist_hits({"a.md": "zorkish"}, ["zork"]), [])
        self.assertEqual(blocklist_hits({"LICENSE": HOLDER.title() + " Funding"}, [HOLDER]), [])
        self.assertTrue(blocklist_hits({"README.md": HOLDER.title() + " Funding"}, [HOLDER]))
        self.assertNotIn("zork", " ".join(blocklist_hits({"a.md": "zork"}, ["zork"])))

    def test_reference_overlay_placeholders_only(self):
        """evals/reference.json: anchors are fact names (no values); entity arguments are placeholders, never literal names or IDs."""
        path = os.path.join(ROOT, "evals", "reference.json")
        if not os.path.exists(path): self.skipTest("no evals/reference.json")
        with open(path, encoding="utf-8") as f: ref = json.load(f)["questions"]
        bad = []
        for qid, e in ref.items():
            for a in e.get("anchors", []):
                if not re.fullmatch(r"[a-z0-9_]+", a): bad.append("%s anchor %r" % (qid, a))
            for c in e.get("calls", []):
                args = c["args"]; params = args.get("params") or {}
                for k, v in list(args.items()) + list(params.items()):
                    if k in ("property", "tenant", "owner", "unit") and not re.fullmatch(r"\{\w+\}", str(v)): bad.append("%s %s=%r" % (qid, k, v))
                for clause in re.split(r"[;]", str(args.get("filters", "")) + ";" + str(args.get("parameters", ""))):
                    if re.search(r"(ID|IDs|Name),(eq|in)?,?\(?\d+", clause): bad.append("%s literal id in %r" % (qid, clause))
        self.assertEqual(bad, [])
        text = open(path, encoding="utf-8").read()
        for term in property_terms():
            self.assertNotRegex(text, r"(?i)(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(term))

    def test_python_floor_agrees(self):
        rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        su = open(os.path.join(ROOT, "setup.py"), encoding="utf-8").read()
        dr = open(os.path.join(ROOT, "doctor.py"), encoding="utf-8").read()
        readme = set(re.findall(r"Python (\d+\.\d+)\+", rd)) | set(re.findall(r"\((\d+\.\d+)\+\)", rd))
        setup = set("%s.%s" % m for m in re.findall(r"version_info < \((\d+), (\d+)\)", su))
        doctor = set("%s.%s" % m for m in re.findall(r"version_info >= \((\d+), (\d+)\)", dr))
        self.assertEqual(len(readme), 1); self.assertEqual(readme, setup); self.assertEqual(setup, doctor)

    def test_ignored_files_not_tracked(self):
        listed = git_tracked()
        if listed is None: self.skipTest("not a git checkout")
        for bad in (".env", ".token_cache.json", "CLAUDE.local.md"):
            self.assertNotIn(bad, listed)
        self.assertEqual([f for f in listed if f.startswith(("spec/", "local/", "out/", ".claude/", "dev-notes/"))], [])

    def test_claude_md_is_the_setup_guide(self):
        """Tracked CLAUDE.md is REQUIRED (the new-user setup guide) and must carry the key safety statements."""
        listed = git_tracked()
        if listed is not None: self.assertIn("CLAUDE.md", listed)
        path = os.path.join(ROOT, "CLAUDE.md")
        self.assertTrue(os.path.exists(path))
        txt = open(path, encoding="utf-8").read()
        for needle in ("BETA", "UNOFFICIAL", "Never ask for", "git clone", "apisupport@rentmanager.com", "View privileges only",
                       "mcp_calls.jsonl", "GitHub issues", "Merge, never overwrite", "WHERE-IT-WORKS"):
            self.assertIn(needle.lower(), txt.lower(), needle)

    def test_where_it_works_setup_link_resolves(self):
        txt = open(os.path.join(ROOT, "docs", "WHERE-IT-WORKS.md"), encoding="utf-8").read()
        guide = open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8").read()
        heads = [re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-") for h in re.findall(r"^#+\s+(.+)$", guide, re.M)]
        anchors = re.findall(r"\]\(\.\./CLAUDE\.md#([a-z0-9-]+)\)", txt)
        self.assertTrue(anchors)
        for a in anchors: self.assertIn(a, heads)


if __name__ == "__main__":
    unittest.main()
