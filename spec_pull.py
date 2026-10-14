#!/usr/bin/env python3
"""Pull API *structure* (not business data) from /APIInformation and /Reports metadata into spec/.
Read-only GETs, throttled by rm.py. Resumable: existing files are skipped unless --refresh.

Usage: python3 spec_pull.py [controllers|all|Model1,Model2] [--refresh] [--skip-enums] [--skip-reports]
  controllers (default): every API controller (resource) + the models they return  (~10 min first run)
  all:                   every model the API knows about (~25 min)
"""
import sys, os, json, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rm

OUT = os.path.join(rm.HERE, "spec")
REFRESH = "--refresh" in sys.argv
for d in ("models", "enums", "reports", "methods"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

def api(path, params=None):
    try:
        s, h, d = rm.get(path, params)
    except Exception as e:  # network hiccup: skip, a re-run resumes
        print("ERR", path, type(e).__name__, flush=True)
        return None
    return d if s == 200 else None

def info(ep, name): return api("/APIInformation/" + ep, {"name": name})

def _path(sub, name): return os.path.join(OUT, sub, name + ".json")

def have(sub, name): return not REFRESH and os.path.exists(_path(sub, name))

def save(sub, name, obj):
    with open(_path(sub, name), "w") as f:
        json.dump(obj, f, indent=1)

def pull_model(m):
    if have("models", m): return
    b = m[:-5] if m.endswith("Model") else m
    f = info("Filters", b + "FilterFields")
    save("models", m, {"model": m, "fields": info("ModelInformation", m),
                       "embeds": info("Embeds", b + "EmbedOptions"),
                       "filters": [x["FilterName"] for x in f] if f else None})

def pull_controller_methods(c):
    if have("methods", c): return
    d = info("Methods", c)
    if d: save("methods", c, d)

def pull_enum(e):
    if have("enums", e): return
    d = info("EnumerationInformation", e)
    if d is not None: save("enums", e, d)

def pull_report(r):
    if have("reports", str(r["ReportID"])): return
    d = api("/Reports/%s" % r["ReportID"], {"embeds": "ReportParameters.ReportParameterValueSource"})
    if d is not None: save("reports", str(r["ReportID"]), d)

def returned_models(models):
    """Models returned by any GET method of any controller (main + sub-resources)."""
    names = set(models)
    out = set()
    for fn in os.listdir(os.path.join(OUT, "methods")):
        for m in json.load(open(os.path.join(OUT, "methods", fn))):
            rm_ = (m.get("GetReturnModel") or "") + "Model"
            if m.get("HasGet") and rm_ in names:
                out.add(rm_)
    return sorted(out)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "controllers"
    t0 = time.time()
    models = api("/APIInformation/Models"); enums = api("/APIInformation/Enumerations")
    ctrls = api("/APIInformation/Controllers")
    if not models or not ctrls:
        sys.exit("Could not read /APIInformation (check .env / run doctor.py)")
    for name, obj in (("models", models), ("enums", enums), ("controllers", ctrls)):
        json.dump(obj, open(os.path.join(OUT, name + ".json"), "w"))
    print(len(ctrls), "controllers,", len(models), "models,", len(enums or []), "enums", flush=True)
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(pull_controller_methods, ctrls)); print("methods done %.0fs" % (time.time() - t0), flush=True)
        todo = models if mode == "all" else returned_models(models) if mode == "controllers" else mode.split(",")
        print("models to pull:", len(todo), flush=True)
        list(ex.map(pull_model, todo)); print("models done %.0fs" % (time.time() - t0), flush=True)
        if "--skip-enums" not in sys.argv:
            list(ex.map(pull_enum, enums or [])); print("enums done %.0fs" % (time.time() - t0), flush=True)
        if "--skip-reports" not in sys.argv:
            reps = api("/Reports", {"fields": "ReportID,Name"}) or []
            json.dump(reps, open(os.path.join(OUT, "reports.json"), "w")); list(ex.map(pull_report, reps))
            print("reports done %d %.0fs" % (len(reps), time.time() - t0), flush=True)
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
