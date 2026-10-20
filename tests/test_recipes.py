#!/usr/bin/env python3
"""Live test of every recipe (read-only). Picks a property and a tenant from the live data, so nothing
company-specific is hardcoded. Prints PASS/FAIL, row counts and latency per recipe.

Usage: python3 tests/test_recipes.py [--portfolio]   (--portfolio also runs non-heavy recipes across all properties)
"""
import os, re, sys, time, json
import tempfile
if "--keep-out" not in sys.argv:   # v4 2c: never write test output into the real out/
    os.environ["RM_OUT"] = tempfile.mkdtemp(prefix="rm_test_out_")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server as S

def pick_property():
    """The active property with the most units (a realistic test)."""
    rows, _, _, _ = S.fetch("/Units", {"fields": "UnitID,PropertyID", "filters": "Property.IsActive,eq,true"}, 100000)
    counts = {}
    for r in rows: counts[r["PropertyID"]] = counts.get(r["PropertyID"], 0) + 1
    return max(counts, key=counts.get)

def pick_tenant(pid):
    """A tenant of that property with an open balance (so open-charge recipes have rows)."""
    for flt in ("PropertyID,eq,%d;Balance,gt,100" % pid, "PropertyID,eq,%d" % pid):
        s, h, d = S.api("/Tenants", {"filters": flt, "fields": "TenantID,Name", "pageSize": 1})
        if s == 200 and d: return d[0]["TenantID"]

def rows_of(text):
    m = re.search(r"has (\d+) row|: (\d+) of (\d+) record", text)
    return int(m.group(1) or m.group(2)) if m else -1

def main():
    pid = pick_property(); tid = pick_tenant(pid)
    print("test property id %s, tenant id %s" % (pid, tid))
    results = []
    for r in S.load_recipes():
        params = {}
        if "property" in (r.get("params") or {}): params["property"] = str(pid)
        if "tenant" in (r.get("params") or {}): params["tenant"] = str(tid); params.pop("property", None)
        runs = [("property", params, False)]
        if "--portfolio" in sys.argv and not r.get("heavy") and "tenant" not in r.get("params", {}):
            runs.append(("portfolio", {k: v for k, v in params.items() if k != "property"}, True))
        for label, p, allp in runs:
            t0 = time.time()
            try:
                out = S.t_run_recipe(r["name"], p)
                ok = not out.startswith("ERROR") and "\nERROR" not in out
                n = rows_of(out)
            except Exception as e:
                out, ok, n = "%s: %s" % (type(e).__name__, e), False, -1
            dt = time.time() - t0
            results.append({"recipe": r["name"], "scope": label, "ok": ok and n != 0, "rows": n, "seconds": round(dt, 1),
                            "error": None if ok else out[:300]})
            print("%-5s %-24s %-9s rows=%-6s %5.1fs %s" % ("PASS" if ok and n != 0 else ("EMPTY" if ok else "FAIL"), r["name"], label, n, dt,
                                                    "" if ok else out[:200].replace("\n", " ")))
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    json.dump(results, open(os.path.join(HERE, "out", "recipe_test_results.json"), "w"), indent=1)
    bad = [x for x in results if not x["ok"]]
    print("%d runs, %d not passing" % (len(results), len(bad)))
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
