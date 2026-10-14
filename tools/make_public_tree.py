#!/usr/bin/env python3
"""Build the clean tree for the public fresh commit. Touches no git state, no remotes, nothing outside the output folder.

  python3 tools/make_public_tree.py [--out DIR] [--no-scan]

Copies exactly the files listed in tools/publish_manifest.txt (patterns allowed) into an EMPTY folder (default: a new
temp folder), then runs the hygiene scan on that folder (tests/test_hygiene.py --root DIR, which also applies the
untracked local/blocklist.txt) and prints the result. Exit code 0 = tree built and clean.
Everything not in the manifest (dev logs, plans, release notes, draft evals, private rules, local data) stays behind.
"""
import fnmatch, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", "local", "out", "spec", ".claude", "dev-notes", "public-tree"}
NEVER = ("CLAUDE.local.md", ".env", ".token_cache.json")


def read_manifest(root=HERE):
    path = os.path.join(root, "tools", "publish_manifest.txt")
    lines = open(path, encoding="utf-8").read().split("\n")
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def disk_files(root=HERE):
    res = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            if f.endswith((".pyc", ".log")): continue
            res.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    return sorted(res)


def expand(patterns, files):
    """-> (selected files, patterns that matched nothing)."""
    chosen, unmatched = [], []
    for p in patterns:
        m = [f for f in files if fnmatch.fnmatch(f, p) and os.path.basename(f) not in NEVER]
        if not m: unmatched.append(p)
        chosen += [f for f in m if f not in chosen]
    return sorted(chosen), unmatched


def build(out, root=HERE, scan=True):
    """Returns dict(files, unmatched, scan_rc, scan_out). Raises ValueError if `out` is not empty."""
    if os.path.exists(out) and os.listdir(out): raise ValueError("output folder is not empty: %s" % out)
    os.makedirs(out, exist_ok=True)
    files, unmatched = expand(read_manifest(root), disk_files(root))
    for rel in files:
        dst = os.path.join(out, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(root, *rel.split("/")), dst)
    res = {"files": files, "unmatched": unmatched, "scan_rc": None, "scan_out": ""}
    if scan:
        r = subprocess.run([sys.executable, os.path.join(root, "tests", "test_hygiene.py"), "--root", out],
                           cwd=root, capture_output=True, text=True)
        res["scan_rc"] = r.returncode
        res["scan_out"] = ((r.stdout or "") + (r.stderr or "")).strip()
    return res


def main(argv):
    out = None; scan = True
    i = 0
    while i < len(argv):
        if argv[i] == "--out": out = os.path.abspath(argv[i + 1]); i += 1
        elif argv[i] == "--no-scan": scan = False
        else: sys.exit(__doc__)
        i += 1
    if out is None: out = os.path.join(tempfile.mkdtemp(prefix="rm-public-tree-"), "tree")
    try:
        res = build(out, HERE, scan)
    except ValueError as e:
        sys.exit(str(e))
    print("Public tree: %s" % out)
    print("%d files copied:" % len(res["files"]))
    for f in res["files"]: print("  " + f)
    if res["unmatched"]: print("MANIFEST ENTRIES THAT MATCHED NOTHING: %s" % ", ".join(res["unmatched"]))
    if scan:
        print("\nHygiene scan on the public tree: %s" % ("PASS" if res["scan_rc"] == 0 else "FAIL"))
        print("\n".join(res["scan_out"].split("\n")[-12:]))
    print("\nNothing was committed, pushed or registered. Publishing needs the owner's go-ahead.")
    sys.exit(1 if res["unmatched"] or (scan and res["scan_rc"] != 0) else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
