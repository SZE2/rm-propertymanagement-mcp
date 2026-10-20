#!/usr/bin/env python3
"""Build spec/index.json: a compact, searchable map of the Rent Manager API built from spec/.
Tolerates partially-present spec dirs (missing files are skipped).

Usage: python3 spec_index.py [--verify]
  --verify  one live GET (pageSize=1) per list resource to record which ones this API user can read
            (status only, no business data stored). ~1 min.

Also importable: load_index(), search(), model_info(), tokens().
"""
import os, re, sys, json, time, functools

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "spec")
INDEX = os.path.join(SPEC, "index.json")

# Method entries that are not useful read endpoints (or are POST-only helpers).
SKIP_SUBS = {"Get", "GetByPost", "Post", "Delete", "CreateUser", "UpdateUser"}

# Generic Rent Manager / property-management vocabulary -> canonical tokens found in resource/report names.
# (Domain words only; nothing company-specific.)
SYNONYMS = {
    "vacant": "vacancy unit availability", "vacancy": "vacancy unit availability", "empty": "vacancy unit",
    "available": "availability vacancy unit", "occupancy": "occupancy unit vacancy", "occupied": "occupancy unit",
    "delinquent": "delinquency receivable balance", "delinquency": "delinquency receivable",
    "arrears": "delinquency receivable", "owe": "delinquency balance receivable", "owes": "delinquency balance receivable",
    "owing": "delinquency balance", "late": "delinquency latefee", "past": "delinquency", "due": "delinquency balance",
    "ar": "receivable delinquency aged", "aging": "aged receivable", "receivables": "receivable",
    "ledger": "transaction charge payment generalledger", "statement": "statement transaction",
    "rentroll": "rentroll rent roll", "roll": "rentroll",
    "expiring": "expiration lease", "expire": "expiration lease", "expiration": "expiration lease",
    "renewal": "renewal lease", "renew": "renewal lease",
    "workorder": "servicemanagerissue issue", "maintenance": "servicemanagerissue issue",
    "repair": "servicemanagerissue issue", "ticket": "servicemanagerissue issue", "work": "servicemanagerissue issue",
    "gl": "glaccount generalledger journal", "general": "generalledger", "chart": "glaccount chart",
    "journal": "journal", "trial": "trialbalance", "noi": "profitloss income",
    "income": "profitloss income", "profit": "profitloss", "loss": "profitloss", "pnl": "profitloss",
    "expense": "expense bill profitloss", "expenses": "expense bill profitloss",
    "investor": "owner", "owners": "owner", "landlord": "owner",
    "contractor": "vendor", "supplier": "vendor", "payable": "bill payable vendor", "payables": "bill payable vendor",
    "deposit": "deposit securitydeposit", "security": "securitydeposit deposit",
    "movein": "moveinmoveout move", "moveout": "moveinmoveout move", "move": "moveinmoveout move",
    "lead": "prospect", "leads": "prospect", "applicant": "prospect application", "application": "application prospect",
    "paid": "payment receipt", "receipt": "payment receipt", "collections": "payment receipt delinquency",
    "billed": "charge", "charges": "charge", "fee": "charge chargetype", "fees": "charge chargetype",
    "apartment": "unit", "apt": "unit", "door": "unit", "doors": "unit",
    "building": "property", "buildings": "property", "site": "property", "portfolio": "property",
    "resident": "tenant", "residents": "tenant", "renter": "tenant", "renters": "tenant", "customer": "tenant",
    "rent": "rent charge recurringcharge", "market": "marketrent", "evict": "eviction", "evictions": "eviction",
    "note": "history note", "notes": "history note", "contact": "contact phone", "phone": "phonenumber contact",
    "email": "contact email", "budget": "budget", "cash": "cashflow bank", "bank": "bank",
}

_STOP = {"the", "a", "an", "of", "for", "in", "on", "by", "to", "and", "or", "with", "all", "list", "show",
         "me", "get", "what", "who", "which", "is", "are", "how", "many", "much", "per", "each", "my", "our",
         "in", "at", "from", "last", "this", "next", "do", "does", "did", "we", "have", "has", "there", "be", "was", "give", "tell", "month", "week", "year", "day", "days"}

def _stem(t):
    if len(t) > 4 and t.endswith("ies"): return t[:-3] + "y"
    if len(t) > 4 and t.endswith("ses"): return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"): return t[:-1]
    return t

def tokens(text):
    """Split camelCase / punctuation, lowercase, stem."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return [_stem(t) for t in re.split(r"[^a-z0-9]+", text.lower()) if t]

def query_terms(q):
    """Query -> {token: weight}; synonyms get weight 0.7. Also joins the raw query (e.g. 'rent roll' -> rentroll)."""
    raw = [t for t in re.split(r"[^a-z0-9&]+", (q or "").lower()) if t]
    out = {}
    joined = "".join(raw)
    if joined: out[_stem(joined)] = 1.0
    for i, t in enumerate(raw):
        if t in _STOP: continue
        out[_stem(t)] = max(out.get(_stem(t), 0), 1.0)
        if i + 1 < len(raw):  # bigram (e.g. "work order" -> workorder, "move in" -> movein)
            bg = t + raw[i + 1]
            for s in SYNONYMS.get(bg, "").split():
                out[_stem(s)] = max(out.get(_stem(s), 0), 0.8)
        for s in SYNONYMS.get(t, "").split():
            out[_stem(s)] = max(out.get(_stem(s), 0), 0.7)
    return out

def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

@functools.lru_cache(maxsize=512)
def model_info(model):
    """Full spec for one model (fields/filters/embeds), loaded lazily from spec/models. Name with or without 'Model'."""
    m = model if model.endswith("Model") else model + "Model"
    d = _load(os.path.join(SPEC, "models", m + ".json"))
    if not d: return None
    return {"model": m[:-5], "fields": d.get("fields") or {}, "filters": d.get("filters") or [],
            "embeds": [e["Name"] for e in (d.get("embeds") or [])]}

def _parse_methods(ctrl, methods):
    main, subs = None, {}
    for m in methods:
        if not m.get("HasGet"): continue
        params = " ".join([m.get("SingleGetParameters", ""), m.get("MultiGetParameters", ""), m.get("GetAllParameters", "")])
        if m["Name"] == "Get":
            main = {"model": m.get("GetReturnModel"), "list": bool(m.get("HasMultiGet") or m.get("HasGetAll")),
                    "by_id": bool(m.get("HasSingleGet"))}
            continue
        if m["Name"] in SKIP_SUBS: continue
        id_in = "id:" in (m.get("SingleGetParameters", "") + m.get("MultiGetParameters", ""))
        extra = sorted({p.split(":")[0] for p in re.split(r",", params) if ":" in p} -
                       {"id", "filters", "embeds", "fields", "orderingOptions", "getOptions", "pageSize", "pageNumber",
                        "filterExpression", "filterFields"})
        subs[m["Name"]] = {"model": m.get("GetReturnModel"), "by_id": id_in, "all": bool(m.get("HasGetAll")),
                           "multi": bool(m.get("HasMultiGet") or m.get("HasGetAll")),
                           **({"params": extra} if extra else {})}
    return main, subs

def _report_params(rep):
    out = []
    for p in rep.get("ReportParameters") or []:
        src = p.get("ReportParameterValueSource") or {}
        if not isinstance(src, dict): src = {}
        hidden = str(p.get("IsHidden", (p.get("ReportParameter") or {}).get("IsHidden", ""))).lower() == "true"
        e = {"key": p.get("ReportParameterID"), "label": p.get("DisplayName") or "", "type": src.get("DataType") or p.get("DataType"),
             "default": src.get("DefaultValue", p.get("DefaultValue"))}
        if hidden: e["hidden"] = True
        if src.get("StaticValues"): e["values"] = [v.get("Value") for v in src["StaticValues"]]
        if src.get("EnumSources"): e["enum"] = src["EnumSources"][0].get("EnumSource")
        if src.get("EnumSources") and src["EnumSources"][0].get("ValuesToExclude"):
            e["exclude"] = src["EnumSources"][0]["ValuesToExclude"]
        if src.get("EntitySources"):
            es = src["EntitySources"][0]; e["ids_of"] = es.get("ModelSource")
        if src.get("AllowsMultipleValues"): e["multi"] = True
        if src.get("IsRequired"): e["required"] = True
        out.append(e)
    return out

def build(verify=False):
    t0 = time.time()
    ctrls = _load(os.path.join(SPEC, "controllers.json"))
    mdir = os.path.join(SPEC, "methods")
    files = set(f[:-5] for f in os.listdir(mdir)) if os.path.isdir(mdir) else set()
    if not ctrls: ctrls = sorted(files)  # older spec dirs: methods filenames are controller names
    resources, by_model, docs = {}, {}, {}
    for c in ctrls:
        methods = _load(os.path.join(mdir, c + ".json"))
        if not methods: continue
        main, subs = _parse_methods(c, methods)
        if not main and not subs: continue
        r = {"model": (main or {}).get("model"), "list": (main or {}).get("list", False), "by_id": (main or {}).get("by_id", False)}
        if subs: r["subs"] = subs
        resources[c] = r
        if r["model"]: by_model.setdefault(r["model"], []).append(c)
        # search terms: name (5), model (4), sub-resource names (2), embeds (1.5), fields (1)
        terms = {}
        def add(ws, w):
            for t in ws: terms[t] = max(terms.get(t, 0), w)
        mi = model_info(r["model"]) if r["model"] else None
        if mi:
            for f in mi["fields"]: add(tokens(f), 1)
            for e in mi["embeds"]:
                if "." not in e: add(tokens(e), 1.5)
            add(tokens(r["model"]), 4)
        for s in subs: add(tokens(s), 2)
        add(tokens(c), 5); terms[c.lower()] = 6
        docs[c] = {k: round(v, 1) for k, v in terms.items()}
    reports = []
    for r in _load(os.path.join(SPEC, "reports.json"), []) or []:
        rep = _load(os.path.join(SPEC, "reports", str(r["ReportID"]) + ".json")) or {}
        reports.append({"id": str(r["ReportID"]), "name": r["Name"], "category": rep.get("ReportCategoryID"),
                        "params": _report_params(rep) if rep else None,
                        "terms": sorted(set(tokens(r["Name"])) | {r["Name"].lower().replace(" ", "")})})
    enums = _load(os.path.join(SPEC, "enums.json"), []) or []
    status = {}
    if verify:
        status = verify_resources(resources)
        for c, s in status.items(): resources[c]["status"] = s
    idx = {"built": time.strftime("%Y-%m-%dT%H:%M:%S"), "resources": resources, "by_model": by_model,
           "docs": docs, "reports": reports, "enums": enums,
           "counts": {"resources": len(resources), "reports": len(reports), "enums": len(enums),
                      "models_on_disk": len(os.listdir(os.path.join(SPEC, "models"))) if os.path.isdir(os.path.join(SPEC, "models")) else 0}}
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, separators=(",", ":"))
    print("index: %d resources, %d reports, %d enums, %.1fs -> %s" % (len(resources), len(reports), len(enums), time.time() - t0, INDEX))
    if status:
        from collections import Counter
        print("verify:", dict(Counter(status.values())))
    return idx

def verify_resources(resources):
    """One GET pageSize=1 per list resource; record HTTP status (200 ok, 204 empty/no privilege, 4xx/5xx)."""
    sys.path.insert(0, HERE)
    import rm
    from concurrent.futures import ThreadPoolExecutor
    def one(c):
        r = resources[c]
        if not r.get("list"): return c, "no-list"
        mi = model_info(r["model"]) if r["model"] else None
        p = {"pageSize": 1}
        if mi and mi["fields"]: p["fields"] = next(iter(mi["fields"]))
        try:
            s, _, _ = rm.get("/" + c, p)
        except Exception as e:
            return c, type(e).__name__
        return c, s
    with ThreadPoolExecutor(6) as ex:
        return dict(ex.map(one, list(resources)))

_IDX = [None, 0]
def load_index():
    """Load (and hot-reload if the file changed) spec/index.json."""
    try:
        mt = os.path.getmtime(INDEX)
    except OSError:
        return None
    if _IDX[0] is None or mt != _IDX[1]:
        _IDX[0], _IDX[1] = _load(INDEX), mt
    return _IDX[0]

_IDF = {}
def _idf(kind):
    """Inverse document frequency per token (cached per index build) so rare words like 'delinquency' outrank 'tenant'."""
    import math
    idx = load_index()
    key = (kind, idx["built"])
    if key not in _IDF:
        docs = [set(t) for t in idx["docs"].values()] if kind == "resources" else [set(r["terms"]) for r in idx["reports"]]
        df = {}
        for d in docs:
            for t in d: df[t] = df.get(t, 0) + 1
        n = len(docs) or 1
        _IDF.clear(); _IDF[key] = {t: 1 + math.log(n / (1 + c)) for t, c in df.items()}
    return _IDF[key]

_KW = [None, None]
def _report_keywords():
    """report_keywords.json: {report name: extra words}; hot-reloaded, missing file = no extras."""
    p = os.path.join(HERE, "report_keywords.json")
    try: mt = os.path.getmtime(p)
    except OSError: return {}
    if _KW[0] != mt: _KW[0], _KW[1] = mt, _load(p, {}) or {}
    return _KW[1]

def search(query, kind="resources", limit=8):
    """Score resources (or reports) against a free-text query. Returns [(score, name)].
    score = sum(query weight * field weight * idf) + bonus when the doc's whole name is covered by the query."""
    idx = load_index()
    if not idx: return []
    qt = query_terms(query)
    idf = _idf(kind)
    out = []
    if kind == "resources":
        items = [(n, t, set(tokens(n))) for n, t in idx["docs"].items()]
    else:
        kw = _report_keywords()
        items = []
        for r in idx["reports"]:
            terms = {t: 5 for t in r["terms"]}
            for t in tokens(kw.get(r["name"], "")): terms[t] = 5
            items.append((r["id"], terms, set(tokens(r["name"]))))
    for name, terms, name_toks in items:
        sc = sum(w * terms.get(t, 0) * idf.get(t, 1) for t, w in qt.items())
        if sc <= 0: continue
        if name_toks:
            cov = sum(max([w for t, w in qt.items() if t == nt] or [0]) for nt in name_toks) / len(name_toks)
            sc += 10 * cov
        if kind == "resources":
            r = idx["resources"][name]
            if not r.get("list"): sc *= 0.6
            if r.get("status") not in (None, 200, 204): sc *= 0.5
        out.append((round(sc, 1), name))
    out.sort(key=lambda x: (-x[0], len(x[1])))
    return out[:limit]

if __name__ == "__main__":
    build(verify="--verify" in sys.argv)
