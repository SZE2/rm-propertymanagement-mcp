#!/usr/bin/env python3
"""Health check for the read-only Rent Manager MCP server. Never prints credentials.

  PY doctor.py     (PY = py -3 on Windows, python3 on macOS/Linux)
Line types: OK = fine. WARN = continue, but read the text (a WARN on a privilege/204 line means that data will be MISSING
from answers until the Rent Manager user is given View privileges / property access). FAIL = stop and fix.
Checks: config, login, locations, privileges (X-RM12Api-CheckedPrivileges), rate-limit headers,
spec/index freshness, aliases, recipes (offline validation against the spec).
"""
import os, sys, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PROBLEMS = []
PY = "py -3" if os.name == "nt" else "python3"

def line(ok, msg):
    print("[%s] %s" % ("OK " if ok is True else "WARN" if ok is None else "FAIL", msg))
    if ok is False: PROBLEMS.append(msg)

def main():
    line(sys.version_info >= (3, 9), "Python %s" % sys.version.split()[0])
    import rm
    keys = [k for k in ("RM_COMPANY", "RM_USERNAME", "RM_PASSWORD") if rm.ENV.get(k)]
    line(len(keys) == 3, ".env has %s%s" % (", ".join(keys) or "nothing", "" if len(keys) == 3 else " (run %s setup.py)" % PY))
    if len(keys) < 3: return finish()
    try:
        t0 = time.time(); rm.get_token(force=True)
        line(True, "login OK (%.1fs)" % (time.time() - t0))
    except Exception as e:
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in "%s %s" % (type(e).__name__, e):
            hint = " (SSL certificate problem: on macOS run 'Install Certificates.command' from /Applications/Python 3.x/, then retry)"
        line(False, "login failed: %s%s" % (e, hint)); return finish()
    saved = rm.ENV.pop("RM_LOCATION", None)
    s, h, d = rm.get("/Current/Locations")
    if saved is not None: rm.ENV["RM_LOCATION"] = saved
    line(s == 200, "locations visible to this user: %s; using %s" % (len(d) if isinstance(d, list) else s,
         ("LocationID " + saved) if saved else "the user's default location"))
    print("Privileges checked by Rent Manager for key reads (X-Checked-Privileges):")
    ACCOUNTING = "/GLAccounts"   # cheap accounting read (needs the 'General ledger accounts: View' privilege); one row only
    for path in ("/Properties", "/Units", "/Tenants", "/Leases", "/Charges", "/Payments", "/Owners", "/Reports", ACCOUNTING):
        s, h, d = rm.get(path, {"pageSize": 1}, extra_headers={"X-RM12Api-CheckedPrivileges": "true"})
        priv = h.get("x-checked-privileges", "-")
        soft = path == ACCOUNTING and s != 200   # accounting: any non-200 is a WARN, never a FAIL
        ok = True if s == 200 else None if (s == 204 or soft) else False
        line(ok, "%-12s HTTP %s  total=%s  privileges: %s%s" % (path, s, h.get("x-total-results", "?"), priv[:150],
             "" if s == 200 else "  -> data from this area will be MISSING from answers: give the API user View privileges / property access in Rent Manager, then re-run %s spec_index.py --verify" % PY))
    lim = {k: v for k, v in rm.LAST_RATE.items() if k in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-resettime")}
    line(True if lim else None, "rate limit: %s (server throttles at %s req/s)" % (lim or "headers not seen", rm.RATE))
    # 5c: out/ size (never deletes anything)
    import server as _S
    od = _S.out_dir(); tot = 0; n = 0
    for dp, _, fs in os.walk(od):
        for f in fs:
            try: tot += os.path.getsize(os.path.join(dp, f)); n += 1
            except OSError: pass
    line(True if tot <= 1 << 30 else None, "output dir %s: %d files, %.1f MB%s" % (od, n, tot / 1048576.0,
         "" if tot <= 1 << 30 else " (over 1 GB; delete old files yourself, doctor never deletes)"))
    # spec / index
    import spec_index as SI
    idx = SI.load_index()
    if not idx:
        line(False, "spec/index.json missing (run: %s spec_pull.py  then  %s spec_index.py --verify)" % (PY, PY) + "")
    else:
        age = (time.time() - os.path.getmtime(SI.INDEX)) / 86400
        line(True if age < 90 else None, "index built %s (%.0f days ago): %s" % (idx["built"], age, idx["counts"]))
        api_ver = h.get("x-api-version")
        st = {}
        for r in idx["resources"].values():
            if "status" in r: st[r["status"]] = st.get(r["status"], 0) + 1
        line(True if st else None, "resource access at index time: %s%s" % (st or "not verified (run spec_index.py --verify)",
             "; API version now %s" % api_ver if api_ver else ""))
    ct = os.path.join(HERE, "local", "charge_types.json")   # rent / subsidy charge-type families
    if not os.path.exists(ct):
        try:
            n = _S.write_charge_types()
            line(True, "local/charge_types.json created: %d rent/subsidy charge types (edit it if your names differ)" % n)
        except Exception as e:
            line(None, "local/charge_types.json missing and could not be created (%s); name patterns are used" % e)
    else:
        try:
            d = json.load(open(ct, encoding="utf-8"))
            if not d.get("rent"):
                line(False, "local/charge_types.json has NO rent types: rent summaries will be empty (edit it, or %s setup.py --charge-types --force)" % PY)
            elif "rent_descriptions" not in d:
                line(None, "local/charge_types.json has no descriptions (ledger rows only carry the description; default patterns are used for them). Regenerate: %s setup.py --charge-types --force" % PY)
            else:
                line(True, "local/charge_types.json: %d rent types, %d subsidy types" % (len(d.get("rent", [])), len(d.get("subsidy", []))))
        except ValueError:
            line(False, "local/charge_types.json is not valid JSON")
    al = os.path.join(HERE, "local", "aliases.json")
    if os.path.exists(al):
        ps = json.load(open(al, encoding="utf-8")).get("properties", [])
        if ps and not all("billing" in p and "address" in p and "owners" in p for p in ps):
            line(None, "local/aliases.json is an old index without billing names, addresses and owners (resolve() cannot find a property by those yet; it rebuilds itself on first use, or run: %s setup.py --aliases)" % PY)
        else:
            line(True, "local/aliases.json: %d properties, %d with nicknames; searchable by name, billing name, address, owner" % (len(ps), sum(1 for p in ps if p.get("aliases"))))
    else:
        line(None, "local/aliases.json missing (%s setup.py --aliases)" % PY + "; resolve() will read /Properties live")
    # recipes: offline validation against spec
    import server as S
    recs = S.load_recipes()
    bad = 0
    for r in recs:
        try:
            req = r["request"]
            if "bundle" in req:
                names = {x["name"] for x in recs}
                miss = [b.get("recipe", b) if isinstance(b, dict) else b for b in req["bundle"]]
                miss = [m for m in miss if isinstance(m, str) and m not in names]
                if miss: raise S.ToolError("bundle references unknown recipes %s" % miss)
            elif "report" in req:
                rep = S.report_by(req["report"])
                if req.get("report_id") is not None and str(req["report_id"]) != str(rep["id"]):
                    print("  WARN recipe %s: report_id %s differs from id %s of '%s' here; using the name" % (r["name"], req["report_id"], rep["id"], rep["name"]))
                keys = {p["key"].lower() for p in rep.get("params") or []}
                missing = [k for k in req.get("parameters", {}) if k.lower() not in keys]
                if missing: raise S.ToolError("unknown report params %s" % missing)
            else:
                q = req["query"]
                res = S.find_resource(q["resource"].replace("{tenant}", "1"))
                _, _, _, _, probs = S.validate(res["model"], q.get("fields", ""), q.get("embeds", ""),
                                               ";".join(c.split("|", 1)[-1] for c in q.get("filters", []) if "{" not in c.split("|", 1)[-1]), q.get("orderby", ""))
                mk = (r.get("post") or {}).get("mark")   # second (flagging) query of a recipe must validate too
                if mk and not probs:
                    _, _, _, _, probs = S.validate(res["model"], mk["key"], "", ";".join(c.split("|", 1)[-1] for c in mk.get("filters", []) if "{" not in c.split("|", 1)[-1]), "")
                if probs: raise S.ToolError("; ".join(probs))
        except Exception as e:
            bad += 1; line(False, "recipe %s: %s" % (r["name"], e))
    line(bad == 0, "recipes: %d loaded, %d invalid against this API spec%s" % (len(recs), bad,
         "" if bad == 0 else " (the other tools and recipes still work: note the recipe names for a bug report and continue only if the user agrees)"))
    finish()

def finish():
    print("\n%s" % ("All good." if not PROBLEMS else "%d FAIL line(s). Stop and fix those first (WARN lines do not block, but read them)." % len(PROBLEMS)))
    sys.exit(1 if PROBLEMS else 0)

if __name__ == "__main__":
    main()
