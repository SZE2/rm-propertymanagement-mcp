#!/usr/bin/env python3
"""Read-only Rent Manager MCP server. Stdlib only; JSON-RPC 2.0 over stdio (newline-delimited).

Run:  python3 server.py            (speaks MCP on stdin/stdout)
      python3 server.py --call TOOL '{"arg": ...}'   (one-off CLI call, for debugging)

Safety: only HTTP GET to the Rent Manager API (plus the token login inside rm.py). No write tools.
Business data is always fetched live; only API structure (spec/) is cached.
"""
import os, sys, re, io, json, time, csv, math, difflib, datetime, threading, traceback, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rm, spec_index as SI
from concurrent.futures import ThreadPoolExecutor as _TPE, TimeoutError as FuturesTimeout

class ThreadPoolExecutor(_TPE):
    """Worker threads inherit the calling thread's `location` (see rm.set_location); ex.map goes through submit."""
    def submit(self, fn, *a, **k):
        return super().submit(rm.bind(fn), *a, **k)

VERSION = "0.11.0-beta"
OUT = os.path.join(HERE, "out")   # 5a: RM_OUT env overrides, read at call time via out_dir()
LOCAL = os.path.join(HERE, "local")
RECIPES = os.path.join(HERE, "recipes")
ALIASES = os.path.join(LOCAL, "aliases.json")
MAX_TEXT = 9000          # max characters returned to the model per call
SHOW_ROWS = 40           # default rows shown inline
INLINE_RESERVE = 1000    # characters of a reply kept free for the top MORE notice, the bottom pointer line and timing
BIG_ROWS, SAMPLE_ROWS = 1000, 10   # defaults: above BIG_ROWS rows only SAMPLE_ROWS are shown inline (RM_BIG_ROWS / RM_SAMPLE_ROWS)
PAGE_CHARS, PAGE_CHARS_MAX = 20000, 60000   # read_result page size: default / ceiling
OPS = {"eq", "ne", "gt", "ge", "lt", "le", "in", "ni", "ct", "sw", "ew", "bt"}

INSTRUCTIONS = """Rent Manager: read-only, LIVE data. Never request passwords in chat. If no property is named, ask which or confirm the whole portfolio. Quote Scope and Answer lines and numbers exactly; do no arithmetic. Prefer run_recipe; use bulk calls, never per-tenant loops. Files are local, not uploaded; share/open only using the client's available file tools, otherwise show the saved path.
1. Recipe fits: run_recipe(name, params) directly; find_recipe is optional. Dates: YYYY-MM-DD, 'September 2026', 'ytd'; ranges: period=.
2. No recipe: list_reports(words) -> run_report(id, parameters). One report covers all properties.
3. PDF: report_pdf(report, properties); for report contents use run_recipe/run_report.
4. Else search_models -> describe_model -> count -> query. Batch ids: UnitID,in,(1,2,3). enum_values lists enums; report_info lists report options.
BIG RESULTS: first line 'MORE:' = rows missing; page with read_result(result_id, offset) or read its File (if your file tools run here). Don't raise show.
RENT = recurring charges + subsidy: query RecurringCharges summary='rent'. Ledger = paid history only.
Vague name: resolve(term) first (names, LLC, addresses, owners; '' lists all); no confident match: ask user. kind=tenant|unit|owner|vendor.
Files: find_attachments -> download_file. locations lists databases; location=name/ID on any tool.
Filters 'Field,op,value' joined by ';'; ops eq ne gt ge lt le in ni ct sw ew bt; nested Property.IsActive,eq,true; orderby -Field."""

# ----------------------------------------------------------------------------- helpers
class ToolError(Exception):
    pass

def _today():
    return datetime.date.today()

_MONTHS = {}
for _i, _n in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1):
    _MONTHS[_n] = _i; _MONTHS[_n[:3]] = _i
_MONTHS["sept"] = 9

def _month_last(y, m):
    return (datetime.date(y, m, 28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)

def _month_of(s):
    """'2026-09' | 'september 2026' | 'sep 2026' | 'sept' -> (year, month) or None. No year = most recent such month."""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12: return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"([a-z]+)\.?,?\s*(\d{4})?", s)
    if m and m.group(1) in _MONTHS:
        mo, t = _MONTHS[m.group(1)], _today()
        return (int(m.group(2)) if m.group(2) else (t.year if mo <= t.month else t.year - 1)), mo
    return None

def _quarter_year(s):
    """'q3 2026' | '2026 q3' | 'this quarter' | 'last quarter' | '2025' | 'last year' -> ((y, first_month), (y, last_month)) or None."""
    t = _today()
    if s in ("this quarter", "current quarter", "last quarter", "previous quarter"):
        qi = (t.month - 1) // 3 + (0 if s[0] in "tc" else -1)
        y = t.year + (qi // 4); qi %= 4
    else:
        m = re.fullmatch(r"(?:q([1-4])[\s,-]*(\d{4})|(\d{4})[\s,-]*q([1-4]))", s)
        if m: qi, y = int(m.group(1) or m.group(4)) - 1, int(m.group(2) or m.group(3))
        elif re.fullmatch(r"\d{4}", s): return (int(s), 1), (int(s), 12)
        elif s in ("last year", "previous year"): return (t.year - 1, 1), (t.year - 1, 12)
        else: return None
    return (y, qi * 3 + 1), (y, qi * 3 + 3)

def parse_date(v, end=False):
    """today | today+N | today-N | month_start | month_end | last_month_start | last_month_end | year_start | t12_start | YYYY-MM-DD
    Also: YYYY-MM, 'September 2026', 'Sep 2026', 'last month', 'this month', 'ytd', 't12' (month/period words give the
    first day, or the last day when end=True)."""
    s = str(v).strip().lower()
    t = _today()
    m = re.fullmatch(r"today\s*([+-])\s*(\d+)", s)
    if s in ("", "today", "now"): d = t
    elif m: d = t + datetime.timedelta(days=int(m.group(2)) * (1 if m.group(1) == "+" else -1))
    elif s == "month_start": d = t.replace(day=1)
    elif s == "month_end": d = (t.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    elif s == "last_month_end": d = t.replace(day=1) - datetime.timedelta(days=1)
    elif s == "last_month_start": d = (t.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    elif s == "year_start": d = t.replace(month=1, day=1)
    elif s == "t12_start":  # first day of the month 12 full months ago (trailing-12 through last_month_end)
        m = t.month - 12 if t.month > 12 else t.month
        d = t.replace(year=t.year - 1, month=m, day=1)
    elif s in ("last month", "last_month", "previous month"):
        return parse_date("last_month_end" if end else "last_month_start")
    elif s in ("this month", "this_month", "current month"):
        return parse_date("month_end" if end else "month_start")
    elif s in ("ytd", "year to date", "year_to_date", "this year"):
        return parse_date("today" if end else "year_start")
    elif s in ("t12", "ttm", "trailing 12", "trailing twelve months", "last 12 months"):
        return parse_date("last_month_end" if end else "t12_start")
    elif _month_of(s):
        y, mo = _month_of(s)
        d = _month_last(y, mo) if end else datetime.date(y, mo, 1)
    elif _quarter_year(s):   # v4 4b: Q3 2026 | 2026 Q3 | this/last quarter | 2025 | last year
        (y, a), (y2, b) = _quarter_year(s)
        d = _month_last(y2, b) if end else datetime.date(y, a, 1)
    else:
        t0 = re.sub(r"^sept\b", "sep", str(v).strip(), flags=re.I)
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%d %B %Y", "%d %b %Y"):
            try:
                d = datetime.datetime.strptime(t0, fmt).date(); break
            except ValueError:
                continue
        else:
            raise ToolError("Bad date '%s'. Use YYYY-MM-DD, YYYY-MM, 'September 2026', 'Q3 2026', 'last month', 'ytd' or today/today+30/month_start (e.g. start='2026-09-01', end='2026-09-30', or period='September 2026')." % v)
    return d.isoformat()

_DATE_WORD = re.compile(r"\b(today(?:\s*[+-]\s*\d+)?|month_start|month_end|last_month_start|last_month_end|year_start|t12_start)\b", re.I)

def expand_dates(s):
    return _DATE_WORD.sub(lambda m: parse_date(m.group(1)), s or "")

def api(path, params=None, timeout=None):
    try:
        return rm.get(path, params, timeout=timeout)
    except rm.RMError as e:
        raise ToolError(str(e))
    except OSError as e:
        if "timed out" in str(e) and timeout and timeout < rm.DEFAULT_TIMEOUT:
            raise ToolError("Too large to size quickly (>%ds); add a date or PropertyID filter." % timeout)
        if "timed out" in str(e):
            raise ToolError("Rent Manager did not answer within %ds; narrow the request (property, dates) and retry." % rm.DEFAULT_TIMEOUT)
        raise ToolError("Network error talking to Rent Manager: %s" % e)

def api_error(s, d):
    if isinstance(d, dict):
        ms = d.get("ModelState")
        if ms: return "HTTP %s: %s" % (s, "; ".join(sum([v if isinstance(v, list) else [str(v)] for v in ms.values()], [])))
        return "HTTP %s: %s" % (s, d.get("DeveloperMessage") or d.get("Message") or d.get("UserMessage"))
    return "HTTP %s: %s" % (s, str(d)[:300])

def idx():
    i = SI.load_index()
    if not i:
        raise ToolError("spec/index.json missing. Run: setup.py (py -3 setup.py on Windows, python3 setup.py on macOS/Linux)")
    return i

def suggest(word, options, n=5):
    opts = list(options)
    low = {o.lower(): o for o in opts}
    hits = difflib.get_close_matches(word.lower(), list(low), n=n, cutoff=0.5)
    sub = [o for o in opts if word.lower() in o.lower() and o.lower() not in hits][: max(0, n - len(hits))]
    return [low[h] for h in hits] + sub

# ----------------------------------------------------------------------------- concepts (where is the data) + charge families
CONCEPTS_FILE = os.path.join(HERE, "concepts.json")
CHARGE_TYPES_FILE = os.path.join(LOCAL, "charge_types.json")

def concepts():
    try:
        with open(CONCEPTS_FILE, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError):
        return {}

def concept_hits(query, limit=3):
    """Concepts whose trigger words appear in the query (whole words / phrases, case-insensitive)."""
    q = " " + re.sub(r"[^a-z0-9&]+", " ", str(query).lower()).strip() + " "
    hits = []
    for c in concepts().get("concepts", []):
        n = max([len(w.split()) for w in c.get("words", []) if " " + re.sub(r"[^a-z0-9&]+", " ", w.lower()).strip() + " " in q] or [0])
        if n: hits.append((n, c))
    hits.sort(key=lambda x: -x[0])
    return [c for _, c in hits[:limit]]

def concept_lines(query):
    out = []
    for c in concept_hits(query):
        out.append("Where is the data (%s): %s%s" % (c["id"], c["pointer"], (" Recipes: " + ", ".join(c["recipes"])) if c.get("recipes") else ""))
    return out

def _qwords(s):
    return " " + re.sub(r"[^a-z0-9&]+", " ", str(s).lower()).strip() + " "

def suggestion_hits(query, limit=2):
    """report_suggestions (concepts.json) whose trigger words appear in the query, best (longest phrase) first."""
    q = _qwords(query)
    hits = []
    for c in concepts().get("report_suggestions", []):
        n = max([len(w.split()) for w in c.get("words", []) if _qwords(w) in q] or [0])
        if n: hits.append((n, c))
    hits.sort(key=lambda x: -x[0])
    return [c for _, c in hits[:limit]]

def suggestion_lines(query):
    return ["Built-in report suggestion (%s): %s" % (c["id"], c["text"]) for c in suggestion_hits(query)]

def suggestion_for_path(path):
    """report_suggestions entry for an API path or resource name ('/Tenants/5/Transactions', 'Charges'), else None."""
    segs = {x.lower() for x in re.split(r"[/?]", str(path)) if x and not x.isdigit()}
    for c in concepts().get("report_suggestions", []):
        if segs & {r.lower() for r in c.get("resources", [])}: return c
    return None

def report_hint(path):
    c = suggestion_for_path(path)
    return c["short"] if c else ""

def model_notes(names):
    """Pointers + filter quirks for describe_model; names = resource/model names (case-insensitive)."""
    low = {str(n).lower() for n in names if n}
    out = []
    for c in concepts().get("concepts", []):
        if low & {m.lower() for m in c.get("on_models", [])}: out.append("Pointer (%s): %s" % (c["id"], c["pointer"]))
    for k, v in (concepts().get("filter_quirks") or {}).items():
        if k.lower() in low: out.append("Filter note (%s): %s" % (k, v))
    return out

# charge-type families: rent = tenant-paid rent charge types; subsidy = rental assistance etc. Both ARE rent (total = sum).
# Ledger rows carry only the charge type DESCRIPTION (no code / ID), recurring-charge rows carry code + description, so the file
# stores both and classification matches either.
_RENT_CODE = {"rc", "u&o", "rent", "mr", "br"}
_RENT_WORD = re.compile(r"\brent\b|use and occupancy|\bu&o\b", re.I)
_RENT_EXCL = re.compile(r"\b(pet|parking|garage|storage|loss|concession|credit|late|fee|fees|deposit|deposits|insurance|discount|employee|"
                        r"violation|against|collection|payment|back|forfeit\w*|forefeit\w*|refund\w*|adjust\w*|waiver|abatement|prepa\w*|"
                        r"free|nsf|legal|court)\b", re.I)
_SUBSIDY_DESC = re.compile(r"rental assistance$|subsid|section ?8|\bs8\b|\bhap\b|\bhcv\b|\bpha\b|\bvash\b|voucher|housing choice|housing and urban|nycha|^hpd$|^ms8$", re.I)
_SUBSIDY_CODE = {"ra", "hap", "hud", "ms8", "hpd", "nycha", "hcv", "pha", "s8", "vash"}

def classify_charge_type(code, desc=""):
    """Default name-pattern classifier: 'rent' | 'subsidy' | None. Rent = the word 'rent' (not rental/renters) or use-and-occupancy
    in the name, minus pet/parking/storage/loss/concession/credit/late/fee/deposit/insurance... types."""
    c, d = str(code or "").strip().lower(), str(desc or "").strip()
    if c in _SUBSIDY_CODE or (d and _SUBSIDY_DESC.search(d) and not re.search(r"loss|credit", d, re.I)): return "subsidy"
    if c in _RENT_CODE: return "rent"
    for txt in (d, str(code or "")):
        if txt and _RENT_WORD.search(txt) and not _RENT_EXCL.search(txt): return "rent"
    return None

_cf_cache = {"mt": None, "path": None, "val": None}

def load_charge_families():
    """local/charge_types.json (written by setup/doctor, user-editable) -> {'rent','subsidy' (codes), 'rent_d','subsidy_d' (descriptions),
    'has_d'} all lower-case sets, or {} when absent. Cached by file mtime."""
    try:
        mt = os.path.getmtime(CHARGE_TYPES_FILE)
    except OSError:
        return {}
    if _cf_cache["path"] == CHARGE_TYPES_FILE and _cf_cache["mt"] == mt and _cf_cache["val"] is not None: return _cf_cache["val"]
    try:
        with open(CHARGE_TYPES_FILE, encoding="utf-8") as f: d = json.load(f)
        lw = lambda k: {str(x).strip().lower() for x in d.get(k, [])}
        val = {"rent": lw("rent"), "subsidy": lw("subsidy"), "rent_d": lw("rent_descriptions"), "subsidy_d": lw("subsidy_descriptions"),
               "has_d": "rent_descriptions" in d or "subsidy_descriptions" in d}
    except (OSError, ValueError, AttributeError):
        return {}
    _cf_cache.update(mt=mt, path=CHARGE_TYPES_FILE, val=val)
    return val

def _desc_match(d, names):
    """Exact description match, or the description starts with a listed one ('Rent Charge 10/2026')."""
    if d in names: return True
    return any(len(n) >= 4 and d.startswith(n + " ") for n in names)

def charge_family(code, desc=""):
    """'rent' | 'subsidy' | None. With the generated file present it is the authority: the code OR the description may match it
    (files from before descriptions were stored fall back to the default classifier on the description)."""
    fam = load_charge_families()
    if fam:
        c, d = str(code or "").strip().lower(), str(desc or "").strip().lower()
        if c and c in fam["subsidy"] or d and d in fam["subsidy"]: return "subsidy"
        if c and c in fam["rent"] or d and d in fam["rent"]: return "rent"
        if d and _desc_match(d, fam["subsidy_d"]): return "subsidy"
        if d and _desc_match(d, fam["rent_d"]): return "rent"
        if not fam["has_d"] and d and (not c or c == d): return classify_charge_type(None, desc)   # ledger-style call (description only) against an old codes-only file
        return None
    return classify_charge_type(code, desc)

def write_charge_types(force=False):
    """Classify this install's ChargeTypes by name patterns into local/charge_types.json (kept if it exists unless force)."""
    if os.path.exists(CHARGE_TYPES_FILE) and not force:
        return -1
    s, h, d = api("/ChargeTypes", {"pageSize": 1000, "fields": "ChargeTypeID,Name,Description,IsActive"})
    if s != 200 or not isinstance(d, list): raise ToolError("Could not list ChargeTypes: " + api_error(s, d))
    fam = {"rent": [], "subsidy": []}; desc = {"rent": [], "subsidy": []}
    for ct in d:
        k = classify_charge_type(ct.get("Name"), ct.get("Description"))
        if k:
            fam[k].append(ct.get("Name"))
            dd = str(ct.get("Description") or "").strip()
            if dd and dd not in desc[k]: desc[k].append(dd)
    os.makedirs(LOCAL, exist_ok=True)
    with open(CHARGE_TYPES_FILE, "w", encoding="utf-8") as f:
        json.dump({"note": "Charge types counted as rent. 'rent' = tenant-paid rent types, 'subsidy' = rental assistance / HAP etc. Both are rent: total rent = rent + subsidy. Match is on the CODE (Name; recurring charges) or the DESCRIPTION (*_descriptions; ledger rows only carry the description). Edit freely; regenerate: python3 server.py --charge-types --force",
                   "generated": time.strftime("%Y-%m-%d"), "rent": fam["rent"], "subsidy": fam["subsidy"],
                   "rent_descriptions": desc["rent"], "subsidy_descriptions": desc["subsidy"]}, f, indent=1)
    rm.private_file(CHARGE_TYPES_FILE)
    return len(fam["rent"]) + len(fam["subsidy"])

def flatten(row, prefix="", out=None, depth=0):
    out = {} if out is None else out
    for k, v in (row or {}).items():
        key = prefix + k
        if isinstance(v, dict) and depth < 3:
            flatten(v, key + ".", out, depth + 1)
        elif isinstance(v, list):
            if v and all(not isinstance(x, (dict, list)) for x in v):
                out[key] = ",".join(str(x) for x in v)
            elif len(v) == 1 and isinstance(v[0], dict) and depth < 3:
                flatten(v[0], key + ".", out, depth + 1)
            else:
                out[key] = json.dumps(v, separators=(",", ":"))[:2000] if v else ""
        else:
            out[key] = v
    return out

def _short_text(items):
    """A list the table cannot spread over rows -> readable one-line text (never cut off, never JSON)."""
    out = []
    for it in items:
        if isinstance(it, dict):
            sc = [(k, v) for k, v in flatten(it).items() if v not in (None, "", [], {})]
            out.append(", ".join("%s=%s" % kv for kv in sc[:4]))
        else:
            out.append(str(it))
    return " ; ".join(out)

def explode(row, prefix="", depth=0, st=None):
    """One raw API row -> list of flat rows. A list of objects (one-to-many embed) becomes ONE ROW PER CHILD with the parent's columns
    repeated (children of children too, along one chain); a second unrelated list in the same row is written as short text instead
    of multiplying rows. Empty list -> empty cell. Scalars lists -> comma text."""
    st = st if st is not None else {"path": None}
    rows = [{}]
    def cross(alts):
        return [dict(a, **b) for a in rows for b in alts]
    for k, v in (row or {}).items():
        key = prefix + k
        if isinstance(v, dict) and depth < 3:
            rows = cross(explode(v, key + ".", depth + 1, st))
        elif isinstance(v, list):
            kids = [x for x in v if isinstance(x, dict)]
            if not v:
                rows = cross([{key: ""}])
            elif not kids and all(not isinstance(x, (dict, list)) for x in v):
                rows = cross([{key: ",".join(str(x) for x in v)}])
            elif kids and len(kids) == len(v) and depth < 3 and (st["path"] is None or key == st["path"] or key.startswith(st["path"] + ".")):
                if st["path"] is None: st["path"] = key   # the first exploded list is the root of the chain; its descendants may explode too
                alts = []
                for x in v: alts += explode(x, key + ".", depth + 1, st)
                rows = cross(alts)
            else:
                rows = cross([{key: _short_text(v)}])
        else:
            rows = cross([{key: v}])
    return rows

def flatten_rows(raw_rows):
    out = []
    for r in raw_rows:
        out += explode(r)
    return out

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _fmt(v, width=40):
    if v is None: return ""
    if isinstance(v, float): v = ("%.2f" % v)
    s = str(v)
    if re.fullmatch(r"\d{4}-\d\d-\d\dT00:00:00", s): s = s[:10]
    s = s.replace("\n", " ").replace("|", "/")
    return s if len(s) <= width else s[: width - 1] + "~"

_END_DATE = re.compile(r"(moveout|todate|enddate|expir|thru|enddt|end_date|lease_?end)", re.I)

def post_process(rows, columns=None, rename=None, group_by=None, sum_cols=None, sort=None, top=None, where=None, running=None, newest_first=None, first_per=None):
    """rows: list of flat dicts. Returns (rows, total_row, notes)."""
    notes = []
    allk = []
    for r in rows:
        for k in r:
            if k not in allk: allk.append(k)
    rows = [r if len(r) == len(allk) else {k: r.get(k) for k in allk} for r in rows]  # same keys on every row
    if where:
        for clause in [w for w in where.split(";") if w.strip()]:
            parts = clause.split(",", 2)
            if len(parts) != 3: raise ToolError("where must be 'Column,op,value' (ops eq ne gt ge lt le ct)")
            col, op, val = parts[0].strip(), parts[1].strip().lower(), parts[2].strip()
            if rows and col not in rows[0]:
                raise ToolError("where: unknown column '%s'. Did you mean: %s" % (col, ", ".join(suggest(col, rows[0].keys()))))
            def ok(r):
                a = r.get(col); na, nv = _num(a), _num(val)
                if op == "ct": return val.lower() in str(a or "").lower()
                if na is not None and nv is not None: a, b = na, nv
                else: a, b = ("" if a is None else str(a)).lower(), val.lower()
                return {"eq": a == b, "ne": a != b, "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}.get(op, True)
            rows = [r for r in rows if ok(r)]
        notes.append("where %s" % where)
    if rename:
        rows = [{rename.get(k, k): v for k, v in r.items()} for r in rows]
    if group_by:
        gcols = [g.strip() for g in group_by.split(",") if g.strip()]
        scols = [s.strip() for s in (sum_cols or "").split(",") if s.strip()]
        if rows:
            for c in gcols + scols:
                if c not in rows[0]:
                    raise ToolError("Unknown column '%s'. Did you mean: %s" % (c, ", ".join(suggest(c, rows[0].keys()))))
        groups = {}
        for r in rows:
            k = tuple(r.get(g) for g in gcols)
            g = groups.setdefault(k, dict(zip(gcols, k), count=0, **{s: 0.0 for s in scols}))
            g["count"] += 1
            for s in scols: g[s] += _num(r.get(s)) or 0.0
        total = dict({gcols[0]: "TOTAL"}, count=sum(g["count"] for g in groups.values()),
                     **{s: round(sum(g[s] for g in groups.values()), 2) for s in scols})
        rows = list(groups.values())
        for r in rows:
            for s in scols: r[s] = round(r[s], 2)
        if not sort: sort = "-" + (scols[0] if scols else "count")
        notes.append("grouped by %s" % group_by)
    else:
        total = None
        if sum_cols and rows:
            scols = [s.strip() for s in sum_cols.split(",") if s.strip()]
            for c in scols:
                if c not in rows[0]: raise ToolError("Unknown column '%s'. Did you mean: %s" % (c, ", ".join(suggest(c, rows[0].keys()))))
            total = {"TOTAL": "TOTAL", "count": len(rows), **{s: round(sum(_num(r.get(s)) or 0 for r in rows), 2) for s in scols}}
    if sort:
        keys = [k.strip() for k in sort.split(",") if k.strip()]
        for k in reversed(keys):
            desc = k.startswith("-"); k = k.lstrip("-+")
            if rows and k not in rows[0]:
                raise ToolError("Unknown sort column '%s'. Did you mean: %s" % (k, ", ".join(suggest(k, rows[0].keys()))))
            rows.sort(key=lambda r: (_num(r.get(k)) is None, _num(r.get(k)) if _num(r.get(k)) is not None else str(r.get(k) or "").lower()), reverse=desc)
            if desc:  # blanks last, except an empty end date (MoveOutDate, ToDate...) = still open = newest, so first
                end = bool(_END_DATE.search(k))
                rows.sort(key=lambda r: (r.get(k) not in (None, "")) if end else (r.get(k) in (None, "")))
    if running and rows:  # cumulative balance, e.g. a tenant ledger
        if running not in rows[0]: raise ToolError("running: unknown column '%s'" % running)
        bal = 0.0
        for r in rows:
            bal += _num(r.get(running)) or 0.0; r["RunningBalance"] = round(bal, 2)
        notes.append("RunningBalance = cumulative %s" % running)
    if newest_first and rows:   # after running balance (computed oldest -> newest): show the newest rows first
        if newest_first not in rows[0]: raise ToolError("most_recent_first: unknown column '%s'. Did you mean: %s" % (newest_first, ", ".join(suggest(newest_first, rows[0].keys()))))
        rows.reverse()   # true reversal of the (oldest-first) input, so same-day rows also run newest to oldest
        rows.sort(key=lambda r: str(r.get(newest_first) or ""), reverse=True)
        end = bool(_END_DATE.search(newest_first))   # empty end date = open lease = newest
        rows.sort(key=lambda r: (r.get(newest_first) not in (None, "")) if end else (r.get(newest_first) in (None, "")))
        notes.append("newest first by %s" % newest_first)
    if first_per and rows:   # keep the first (after sorting) row per key, e.g. newest lease per UnitID
        if first_per not in rows[0]: raise ToolError("first_per: unknown column '%s'. Did you mean: %s" % (first_per, ", ".join(suggest(first_per, rows[0].keys()))))
        seen, kept, nokey = set(), [], 0
        for r in rows:
            k = r.get(first_per)
            if k in (None, ""):   # no key: never merge unrelated rows into one group
                kept.append(r); nokey += 1; continue
            if k in seen: continue
            seen.add(k); kept.append(r)
        notes.append("first row per %s (%d of %d%s)" % (first_per, len(kept), len(rows), "; %d row(s) without a key kept as is" % nokey if nokey else ""))
        rows = kept
    if columns:
        cols = [c.strip() for c in columns.split(",") if c.strip()]
        if rows:
            bad = [c for c in cols if c not in rows[0]]
            if bad: raise ToolError("Unknown column(s) %s. Available: %s" % (bad, ", ".join(list(rows[0].keys())[:60])))
        rows = [{c: r.get(c) for c in cols} for r in rows]
    if top:
        rows = rows[: int(top)]
    return rows, total, notes

# ----------------------------------------------------------------------------- 5a output dir / 5d PII masking
def out_dir():
    return os.environ.get("RM_OUT") or OUT

_PII_KEY = re.compile(r"taxid|taxpayerid|ssn|socialsec|phone|email|dob|birth|bank(account|acct|routing|number)|routing|accountnumber|acctnumber", re.I)

def mask_on():
    return os.environ.get("RM_MASK_PII", "").strip().lower() in ("1", "true", "yes", "on")

def pii_key(key):
    return bool(_PII_KEY.search(str(key)))

def mask_value(v):
    """Keep the last 4 alphanumerics, replace the rest with * (same length). Empty/None unchanged."""
    if v is None or v == "" or isinstance(v, bool): return v
    t = str(v)
    keep = [i for i, c in enumerate(t) if c.isalnum()]
    hide = set(keep[:-4])
    return "".join("*" if i in hide else c for i, c in enumerate(t))

def mask_raw(o, inherited=False):
    """Recursively mask scalar values under PII-named keys. Returns o unchanged (same object) when masking is off."""
    if not mask_on(): return o
    if isinstance(o, dict):
        return {k: mask_raw(v, inherited or pii_key(k)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask_raw(v, inherited) for v in o]
    return mask_value(o) if inherited else o

def mask_rows(rows):
    if not mask_on() or not rows: return rows
    return [{k: (mask_value(v) if pii_key(k) and not isinstance(v, (dict, list)) else v) for k, v in r.items()} for r in rows]

_PII_TEXT = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+|\b\d{3}-\d{2}-\d{4}\b")
def mask_args(o):
    """Call-log args: mask values under PII keys, and emails / SSN patterns inside strings."""
    if not mask_on(): return o
    if isinstance(o, dict):
        return {k: (mask_raw({k: v}, False)[k] if pii_key(k) else mask_args(v)) for k, v in o.items()}
    if isinstance(o, list): return [mask_args(v) for v in o]
    if isinstance(o, str): return _PII_TEXT.sub(lambda m: mask_value(m.group(0)), o)
    return o

def table(rows, limit=SHOW_ROWS, total=None):
    if not rows: return "(no rows)"
    rows = mask_rows(rows)
    total = mask_rows([total])[0] if total else total
    cols = []
    for r in rows[:limit]:
        for k in r:
            if k not in cols: cols.append(k)
    cols = [c for c in cols if any(r.get(c) not in (None, "", "[]") for r in rows[:limit])] or cols
    lines = ["|".join(cols)]
    for r in rows[:limit]:
        lines.append("|".join(_fmt(r.get(c)) for c in cols))
    if total:
        lines.append("|".join(_fmt(total.get(c, "TOTAL" if i == 0 else "")) for i, c in enumerate(cols)))
    return "\n".join(lines)

def save(label, raw, flat_rows):
    od = out_dir()
    os.makedirs(od, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_")[:60] or "result"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 0
    while True:   # v4 1b: exclusive create; suffix on same-second collision
        jp = os.path.join(od, "%s_%s%s.json" % (base, stamp, "" if n == 0 else "-%d" % (n + 1)))
        try:
            f = open(jp, "x", encoding="utf-8"); break
        except FileExistsError:
            n += 1
    with f:
        json.dump(mask_raw(raw), f, separators=(",", ":"), default=str)   # 5b: compact
    cp = None
    if flat_rows:
        cp = jp[:-5] + ".csv"
        write_csv(cp, flat_rows)
    return jp, cp

def write_csv(path, rows):
    rows = mask_rows(rows)
    cols = []
    for r in rows:
        for k in r:
            if k not in cols: cols.append(k)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

FILE_HINT = ("Show this file to the user in the chat if you can: use your file-sharing or attach tool (for example SendUserFile in Claude Code, "
             "copy it to the outputs folder in Cowork, or use a file panel/link in ChatGPT desktop or Codex) with the path above, "
             "then tell the user they can save it from there. If you cannot attach files, give the full path and say it is on their computer. "
             "Only say it is attached or uploaded after that action succeeds.")

def file_lines(*paths, hint=True):
    """'File: <full absolute path>' on its own line per saved file (easy to open), then one reminder line for the model."""
    lines = ["File: %s" % os.path.abspath(p) for p in paths if p]
    if lines and hint: lines.append(FILE_HINT)
    return "\n".join(lines)

_NOTICE = re.compile(r"^MORE: \d+ of \d+ rows shown")
_LEAD = ("CAPPED", "PARTIAL")

def lift_notice(text):
    """Moves every 'MORE: K of N rows shown' notice line to the top of the reply, right after any leading CAPPED/PARTIAL lines
    (the model reads top to bottom; the bottom of a long reply can be cut). Idempotent."""
    if "MORE: " not in text: return text
    lines = text.split("\n")
    notes = [l for l in lines if _NOTICE.match(l)]
    if not notes: return text
    rest = [l for l in lines if not _NOTICE.match(l)]
    i = 0
    while i < len(rest) and rest[i].startswith(_LEAD): i += 1
    return "\n".join(rest[:i] + notes + rest[i:])

def clip(text):
    """Safety net (tables are already fitted by fit_table): lifts the MORE notice to the top, then cuts at a line boundary, says how
    much was cut, and KEEPS the first lines (CAPPED + MORE) and the closing pointer/File lines so the model can still reach the full data."""
    text = lift_notice(text)
    if len(text) <= MAX_TEXT: return text
    k = text.rfind("\nSee MORE above")
    if k < 0: k = text.find("\nFile: ")
    tail = text[k:] if k >= 0 and len(text) - k <= 2500 else ""
    head = text[:MAX_TEXT - len(tail) - 80]
    top = 0   # end of the leading CAPPED/PARTIAL + MORE lines: never cut into them
    for l in text.split("\n"):
        if not (l.startswith(_LEAD) or _NOTICE.match(l)): break
        top += len(l) + 1
    if len(head) < top: head = text[:top - 1]
    elif "\n" in head and head[top:].count("\n"): head = head[:head.rfind("\n")]
    return "%s\n...[%d characters of this reply cut here]%s" % (head, len(text) - len(head) - len(tail), tail)

# ----------------------------------------------------------------------------- result delivery (inline / read_result / file)
def delivery_mode():
    """RM_DELIVERY: auto (default) offers read_result AND the local path; mcp = read_result only (no host paths, for clients that
    can never see this computer's disk); file = local path only."""
    m = (os.environ.get("RM_DELIVERY") or "auto").strip().lower()
    return m if m in ("auto", "mcp", "file") else "auto"

def fit_table(rows, limit, total=None, budget=MAX_TEXT - INLINE_RESERVE):
    """table() of at most `limit` rows that also fits `budget` characters (whole rows only). Returns (text, rows shown)."""
    budget = max(int(budget), 1500)
    n = max(0, min(int(limit), len(rows)))
    text = table(rows, n, total)
    while n > 1 and len(text) > budget:
        n = max(1, min(n - 1, int(n * budget / len(text))))
        text = table(rows, n, total)
    return text, (n if rows else 0)

def result_id(path):
    return os.path.splitext(os.path.basename(path))[0] if path else ""

def save_view(jp, rows):
    """The table as shown (after columns/group_by/sort/rename) next to the raw save, so read_result pages exactly that table."""
    stem = result_id(jp)
    vstem = re.sub(r"(_\d{8}-\d{6}(?:-\d+)?)$", r"_view\1", stem)
    vp = os.path.join(os.path.dirname(jp), (vstem if vstem != stem else stem + "_view") + ".csv")
    write_csv(vp, rows)
    return vp

def big_rows():
    return _env_int("RM_BIG_ROWS", BIG_ROWS)

def sample_rows():
    return _env_int("RM_SAMPLE_ROWS", SAMPLE_ROWS)

def show_plan(show, n):
    """(rows to show inline, large?) for a table of n rows. An explicit `show` always wins; otherwise a table over RM_BIG_ROWS rows
    shows only RM_SAMPLE_ROWS rows (saves the model's context; the notice says how to get the rest) and large is True."""
    if show not in (None, "", 0): return int(show), False
    if n > big_rows(): return sample_rows(), True
    return SHOW_ROWS, False

def deliver(shown, nshown, jp, cp, saved_rows=None, large=False):
    """Lines after an inline table. All rows shown: the saved file(s). Otherwise two lines: the 'MORE: K of N rows shown ...' notice
    with BOTH ways to the rest (read_result works in every client, through MCP; the local file only for agents whose file tools run on
    this computer) and ONE short pointer line. lift_notice/clip move the notice to the top of the reply."""
    mode = delivery_mode()
    if nshown >= len(shown):
        return file_lines(jp, cp, hint=False) if mode != "mcp" else ""
    src = cp if (cp and saved_rows is not None and shown == saved_rows) else save_view(jp, shown)
    rid, path = result_id(src), os.path.abspath(src)
    rr = "read_result(result_id='%s', offset=%d)" % (rid, nshown)
    note = "MORE: %d of %d rows shown%s; the rest is NOT in this reply. " % (nshown, len(shown), " (LARGE RESULT: %d rows)" % len(shown) if large else "")
    if mode == "mcp": note += "%s pages it (any client)" % rr
    elif mode == "file": note += "File: %s has all rows (if your file tools run on this computer)" % path
    else: note += "%s pages it (any client); or File: %s if your file tools run on this computer" % (rr, path)
    note += "; or narrow the request (date range, property) or use a built-in report."
    return "%s\nSee MORE above: %s" % (note, ("File: " + path) if mode == "file" else rr)

_RID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}$")

def load_result(rid):
    """Rows + columns of a saved result in out_dir() (CSV preferred, else the raw JSON flattened)."""
    for ext in (".csv", ".json"):
        p = os.path.join(out_dir(), rid + ext)
        if os.path.isfile(p): break
    else:
        raise ToolError("No saved result '%s' on this server (results are deleted after RM_OUT_KEEP_DAYS days). Re-run the query." % rid)
    if ext == ".csv":
        with open(p, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            rows = list(rd)
            cols = list(rd.fieldnames or [])
    else:
        with open(p, encoding="utf-8") as f: d = json.load(f)
        rows = flatten_rows(d if isinstance(d, list) else [d])
        cols = []
        for r in rows:
            for k in r:
                if k not in cols: cols.append(k)
    return p, mask_rows(rows), cols

_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00$")

def _page_val(v):
    """CSV cell for read_result: None -> '', a midnight timestamp -> its date (same value as the inline table, fewer characters)."""
    if v is None: return ""
    m = _MIDNIGHT.match(v) if isinstance(v, str) else None
    return m.group(1) if m else v

def t_read_result(result_id, offset=0, limit=0, columns="", max_chars=PAGE_CHARS):
    rid = re.sub(r"\.(csv|json)$", "", os.path.basename(str(result_id or "").strip().replace("\\", "/")), flags=re.I)
    if not _RID.match(rid): raise ToolError("result_id must be the id from a MORE line, e.g. Leases_20261018-161931")
    p, rows, cols = load_result(rid)
    if columns:
        want = [c.strip() for c in str(columns).split(",") if c.strip()]
        bad = [c for c in want if c not in cols]
        if bad: raise ToolError("Unknown column(s) %s. Columns: %s" % (", ".join(bad), ", ".join(cols)))
        cols = want
    n = len(rows)
    if not n: return "Result %s has no rows." % rid
    start = max(0, int(offset or 0))
    if start >= n: raise ToolError("offset %d is past the end: result %s has %d rows (offsets 0-%d)." % (start, rid, n, n - 1))
    stop = n if not limit else min(n, start + max(1, int(limit)))
    budget = max(1000, min(int(max_chars or PAGE_CHARS), PAGE_CHARS_MAX))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    i = start
    while i < stop:   # whole rows until the page budget is used (at least one row)
        mark = buf.tell()
        w.writerow([_page_val(rows[i].get(c)) for c in cols])
        if buf.tell() > budget and i > start:
            buf.seek(mark); buf.truncate(); break
        i += 1
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
    nxt = ("Next: read_result(result_id='%s', offset=%d%s)" % (rid, i, ", columns='%s'" % columns if columns else "") if i < n
           else "All %d rows delivered." % n)
    return "Result %s: rows %d-%d of %d as CSV (saved %s; the saved result, not re-queried):\n%s\nEND OF PAGE rows %d-%d of %d. %s" % (
        rid, start + 1, i, n, when, buf.getvalue().rstrip("\n"), start + 1, i, n, nxt)

# ----------------------------------------------------------------------------- properties / scope
def _addr_text(a):
    """One address dict (Street/City/State/PostalCode) -> 'street, city, ST zip' or ''."""
    if not isinstance(a, dict): return ""
    tail = " ".join(x for x in (a.get("State"), a.get("PostalCode")) if x)
    return ", ".join(x for x in (a.get("Street"), a.get("City"), tail) if x)

def _live_properties():
    """ONE /Properties call: names, billing names, primary (else billing) address, current owners. Index fields: billing, address, owners."""
    s, h, d = api("/Properties", {"fields": "PropertyID,Name,ShortName,IsActive,BillingName1,BillingName2,PrimaryAddress,BillingAddress,CurrentOwners",
                                  "embeds": "PrimaryAddress,BillingAddress,CurrentOwners", "pageSize": 5000})
    if s != 200 or not isinstance(d, list): raise ToolError("Could not list properties: " + api_error(s, d))
    out = []
    for p in d:
        owners = []
        for o in p.get("CurrentOwners") or []:
            nm = (o.get("Name") or o.get("DisplayName") or "").strip() if isinstance(o, dict) else ""
            if nm and nm not in owners: owners.append(nm)
        out.append({"id": p["PropertyID"], "name": p.get("Name") or "", "short": p.get("ShortName") or "",
                    "active": bool(p.get("IsActive")), "aliases": [],
                    "billing": [x.strip() for x in (p.get("BillingName1"), p.get("BillingName2")) if x and x.strip()],
                    "address": _addr_text(p.get("PrimaryAddress")) or _addr_text(p.get("BillingAddress")), "owners": owners})
    return out

_INDEX_REBUILT = [False]

def _index_current(props):
    return all("billing" in p and "address" in p and "owners" in p for p in props)

_LOC_PROPS = {}

def properties(live=False):
    """Property index from local/aliases.json (user-editable nicknames) else live. live=True skips the file.
    A file from before v10 (no billing/address/owners) is rebuilt once per process (nicknames kept); if that fails the old names are used."""
    if live: return _live_properties()
    loc = rm.current_location()
    if loc:   # another location = another database: never use local/aliases.json (default location); live list, cached per process
        if loc not in _LOC_PROPS:
            _LOC_PROPS[loc] = _live_properties()
        return _LOC_PROPS[loc]
    props = None
    try:
        with open(ALIASES, encoding="utf-8") as f:
            props = json.load(f).get("properties")
    except (OSError, ValueError):
        pass
    if props:
        if not _index_current(props) and not _INDEX_REBUILT[0]:
            _INDEX_REBUILT[0] = True
            try:
                write_aliases_stub()
                with open(ALIASES, encoding="utf-8") as f: props = json.load(f).get("properties") or props
            except Exception:
                pass   # keep the old names-only index; doctor warns
        return props
    return _live_properties()

def write_aliases_stub():
    """Create/refresh local/aliases.json from live /Properties, keeping any aliases the user already added."""
    os.makedirs(LOCAL, exist_ok=True)
    old = {}
    try:
        with open(ALIASES, encoding="utf-8") as f: old = {p["id"]: p.get("aliases", []) for p in json.load(f)["properties"]}
    except Exception:
        pass
    props = _live_properties()
    for p in props: p["aliases"] = old.get(p["id"], [])
    with open(ALIASES, "w", encoding="utf-8") as f:
        json.dump({"note": "Add nicknames to 'aliases' (e.g. [\"the tower\", \"bldg 5\"]). billing/address/owners are filled from Rent Manager. Regenerate: python3 setup.py --aliases",
                   "generated": time.strftime("%Y-%m-%d"), "properties": props}, f, indent=1)
    return len(props)

def _norm(s): return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()

CONFIDENT = 60   # best score below this = 'no confident match' (never guess)
_STOP = {"llc", "inc", "corp", "co", "ltd", "lp", "llp", "the", "of", "and", "at", "properties", "realty"}
# field label -> score penalty: name/short/alias > billing name > address > owner name; small so a substring hit stays >= CONFIDENT
_TIERS = {"name": 0, "short name": 0, "alias": 0, "billing name": 2, "address": 4, "owner": 6}

def _score(term_n, nn, loose):
    """0-100 for one normalised term vs one normalised text. loose (billing/address/owner): legal-suffix words do not count as shared words."""
    if not nn: return 0
    if loose and not (set(term_n.split()) - _STOP): return 0   # only legal-suffix words (LLC, Inc...): not a name
    if term_n == nn: return 100
    if nn.startswith(term_n) or term_n.startswith(nn): return 80
    if term_n in nn: return 70
    tw, nw = set(term_n.split()), set(nn.split())
    if loose: tw -= _STOP; nw -= _STOP
    if tw & nw: return 40 + 10 * len(tw & nw)
    return int(60 * difflib.SequenceMatcher(None, term_n, nn).ratio()) - 10

def match_detail(term, n=6, props=None):
    """-> [(score, property, field, matched_text)] best first. A billing/owner-name hit returns every property sharing it (cap 300)."""
    term_n = _norm(term)
    out = []
    for p in (props if props is not None else properties()):
        cands = [("name", p.get("name")), ("short name", p.get("short"))] + [("alias", x) for x in p.get("aliases") or []]
        cands += [("billing name", x) for x in p.get("billing") or []] + [("address", p.get("address"))] + [("owner", x) for x in p.get("owners") or []]
        best = (0, "", "")
        for fld, txt in cands:
            nn = _norm(txt or "")
            if not nn: continue
            sc = _score(term_n, nn, fld in ("billing name", "address", "owner"))
            sc -= _TIERS[fld] if sc < 100 else 0
            if sc > best[0]: best = (sc, fld, txt)
        if term_n == str(p["id"]): best = (100, "id", str(p["id"]))
        sc = best[0] - (5 if p.get("active") is False else 0)
        if sc >= 35: out.append((sc, p, best[1], best[2]))
    out.sort(key=lambda x: -x[0])
    cap = 300 if out and out[0][2] in ("billing name", "owner") else n
    return out[:cap]

def match_properties(term, n=6, props=None):
    return [(sc, p) for sc, p, _f, _t in match_detail(term, n, props)]

def _cands(m, k=5):
    return "; ".join("%s=ID %s (%s: %s)" % (p["name"], p["id"], f, t) for _, p, f, t in m[:k])

def resolve_property_ids(value):
    """'Oak Court' / '12' / 'Oak Court, Elm' / [12, 'Elm'] -> ([ids], label). Raises ToolError with candidates when ambiguous or not confident.
    A term that is one owner/billing name resolves to all properties sharing it."""
    if value in (None, "", [], "all", "*"): return [], None
    if isinstance(value, str):
        value = re.sub(r"(?i)\b(?:property\s*)?(?:id|#)\s*[:=#]?\s*(?=\d)", "", value)
    items = value if isinstance(value, list) else [x for x in re.split(r"[,;]", str(value).strip("() ")) if x.strip()]
    ids, labels = [], []
    props = {p["id"]: p for p in properties()}
    refreshed = [False]
    def refresh():
        """The local alias file can be stale (new property): retry once against the live list."""
        if refreshed[0]: return False
        refreshed[0] = True
        try: live = _live_properties()
        except ToolError: return False
        for p in live:
            old = props.get(p["id"])
            p["aliases"] = (old or {}).get("aliases", [])
            props[p["id"]] = p
        return True
    for it in items:
        it = str(it).strip()
        if it.isdigit() and int(it) not in props: refresh()
        if it.isdigit() and int(it) in props:
            ids.append(int(it)); labels.append("%s (ID %s)" % (props[int(it)]["name"], it)); continue
        m = match_detail(it, props=list(props.values()))
        if (not m or m[0][0] < 70) and refresh():
            m = match_detail(it, props=list(props.values()))
        if not m: raise ToolError("No property matches '%s'. Ask the user which property, or call resolve('') to list them, then e.g. params={\"property\": \"Oak Court\"} or the numeric ID." % it)
        if m[0][0] < CONFIDENT:
            raise ToolError("No confident match for '%s'. Closest: %s. Ask the user which property (or give the numeric ID)." % (it, _cands(m)))
        top = m[0]
        if top[2] in ("billing name", "owner"):   # one LLC/owner name -> all its properties
            grp = [x for x in m if x[0] == top[0] and x[2] == top[2] and _norm(x[3]) == _norm(top[3])]
            if len(m) == len(grp) or m[len(grp)][0] < top[0] - 15:
                for _, p, _f, _t in grp:
                    ids.append(p["id"]); labels.append("%s (ID %s)" % (p["name"], p["id"]))
                continue
        if len(m) > 1 and m[0][0] < 100 and m[0][0] - m[1][0] < 15:
            raise ToolError("'%s' is ambiguous: %s. Ask the user which one, then pass the ID." % (it, _cands(m)))
        p = top[1]; ids.append(p["id"]); labels.append("%s (ID %s)" % (p["name"], p["id"]))
    return ids, "Property: " + ", ".join(labels)

def all_scope_label():
    try:
        ps = properties()
        act = sum(1 for p in ps if p.get("active", True))
        return "All properties the API user can see (%d active of %d)" % (act, len(ps))
    except ToolError:
        return "All properties"

def scope_warning(what):
    """Soft scope guard: heavy portfolio-wide runs are allowed; we only warn (user decision)."""
    return ("Note: %s ran portfolio-wide (no property given; heavy reports can take 20-40s). If the user meant one property, "
            "rerun with property='<name or ID>' (resolve('') lists properties)." % what)

# ----------------------------------------------------------------------------- resources & validation
def find_resource(path):
    """'Tenants' | 'tenants/123' | 'Tenants/123/Transactions' | 'Tenants/OpenReceivables' -> dict."""
    i = idx()
    parts = [p for p in str(path).strip().strip("/").split("/") if p]
    if not parts: raise ToolError("resource is required, e.g. 'Units' or 'Tenants/123/Transactions'")
    res = {k.lower(): k for k in i["resources"]}
    ctrl = res.get(parts[0].lower())
    if not ctrl:
        by_model = {k.lower(): v[0] for k, v in i["by_model"].items()}
        ctrl = by_model.get(parts[0].lower()) or by_model.get(parts[0].lower().rstrip("s"))
    if not ctrl:
        hits = [n for _, n in SI.search(parts[0], limit=5)]
        raise ToolError("Unknown resource '%s'. Did you mean: %s" % (parts[0], ", ".join(suggest(parts[0], i["resources"]) + hits)))
    r = i["resources"][ctrl]
    out = {"ctrl": ctrl, "id": None, "sub": None, "model": r.get("model"), "list": r.get("list"), "status": r.get("status")}
    rest = parts[1:]
    if rest and rest[0].isdigit():
        out["id"] = rest[0]; rest = rest[1:]; out["list"] = False
    if rest:
        subs = {k.lower(): k for k in (r.get("subs") or {})}
        sub = subs.get(rest[0].lower())
        if not sub:
            raise ToolError("'%s' has no sub-resource '%s'. Options: %s" % (ctrl, rest[0], ", ".join(suggest(rest[0], subs.values(), 8) or list(subs.values())[:30])))
        sd = r["subs"][sub]
        out.update(sub=sub, model=sd.get("model"), list=sd.get("multi", False), sub_all=out["id"] is None)
        if out["id"] is None and not sd.get("all"):
            raise ToolError("%s/%s needs an ID: use %s/{id}/%s" % (ctrl, sub, ctrl, sub))
        if out["id"] is not None and not sd.get("by_id"):
            raise ToolError("%s/%s does not take an ID: use %s/%s" % (ctrl, sub, ctrl, sub))
    out["path"] = "/" + "/".join([ctrl] + ([out["id"]] if out["id"] else []) + ([out["sub"]] if out["sub"] else []))
    return out

def split_top(s, sep=";"):
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(": depth += 1
        elif ch == ")": depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip(): parts.append(cur)
    return [p.strip() for p in parts if p.strip()]

_Q = r"""(?:"[^"]*"|'[^']*')"""
_IN_PAREN = re.compile(r"""([A-Za-z_][\w.]*)\s*,\s*(in|ni|bt)\s*,\s*\(((?:%s|[^()"'])*)\)""" % _Q, re.I)
_IN_BARE = re.compile(r"([A-Za-z_][\w.]*)\s*,\s*(in|ni)\s*,\s*(\d+(?:\s*[;,]\s*\d+)*)(?![\w(.])", re.I)
_BT_BARE = re.compile(r"([A-Za-z_][\w.]*)\s*,\s*(bt)\s*,\s*([\d][\d\-:.T]*)\s*[;,]\s*([\d][\d\-:.T]*)(?![\w(.:-])", re.I)
_IN_EMPTY = re.compile(r"([A-Za-z_][\w.]*)\s*,\s*(in|ni|bt)\s*,\s*(?:\(\s*\))?\s*(?:;|$)", re.I)

def split_items(text):
    """Split a list on ',' / ';' outside quotes; quoted values keep their commas, spaces and quotes."""
    items, cur, q = [], "", ""
    for ch in text:
        if q:
            cur += ch
            if ch == q: q = ""
        elif ch in "\"'":
            q = ch; cur += ch
        elif ch in ",;":
            if cur.strip(): items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip(): items.append(cur.strip())
    return items

def normalize_in(filters):
    """Accept 'F,in,1;2;3', 'F,in,1,2,3', 'F,in,(1;2;3)', 'F,in,(1, 2)', 'F,in,("Smith, John",Doe)' -> 'F,in,(1,2,3)'. ';' separates
    clauses, so a bare list is only recognised when every item is a number (names need the parenthesised form; quote values that
    contain commas). 'F,bt,a;b' / 'F,bt,a,b' (numbers/dates) -> 'F,bt,(a,b)'. An empty list is an error."""
    filters = filters or ""
    m0 = _IN_EMPTY.search(filters)
    if m0: raise ToolError("filter '%s,%s,' has an empty list. Give values: %s,%s,(1,2,3) (an empty list matches nothing; skip the filter instead)." % (m0.group(1), m0.group(2).lower(), m0.group(1), m0.group(2).lower()))
    def fix(m):
        items = split_items(m.group(3))
        if not items: raise ToolError("filter '%s,%s,()' has an empty list. Give values or drop the filter." % (m.group(1), m.group(2).lower()))
        if m.group(2).lower() == "bt" and len(items) != 2: raise ToolError("bt needs exactly two values: %s,bt,(from,to)" % m.group(1))
        return "%s,%s,(%s)" % (m.group(1), m.group(2).lower(), ",".join(items))
    f = _IN_PAREN.sub(fix, filters)
    f = _IN_BARE.sub(fix, f)
    return _BT_BARE.sub(lambda m: "%s,bt,(%s,%s)" % (m.group(1), m.group(3), m.group(4)), f)

def bad_filter(model, f):
    """Known-broken filter names for a model (concepts.json bad_filters): nested owner filters on history notes."""
    for b in (concepts().get("bad_filters") or {}).get(model, []):
        if f == b or f.startswith(b + "."): return True
    return False

def filterable_hint(model, f, mi, flt):
    """Closest filterable names + alias hint for a bad filter field."""
    al = {k.lower(): v for k, v in (concepts().get("field_aliases") or {}).items()}
    hint = ""
    if f.lower() in al: hint = " Use: %s." % al[f.lower()]
    tops = [x for x in flt if x.count(".") <= 1]
    close = suggest(f, tops)
    sup = [] if f in mi["fields"] else [x for x in tops if f.lower() in x.lower()][:3]
    note = " Not filterable (it is a plain field/embed): filter another field, or fetch it and use where='%s,op,value'." % f if f in mi["fields"] or f in mi["embeds"] else ""
    return "filter field '%s' not filterable on %s.%s Close filterable fields: %s.%s (describe_model(section=filters) lists all)" % (
        f, model, hint, ", ".join(close or sup) or "(none)", note)

def validate(model, fields="", embeds="", filters="", orderby=""):
    """Check names against the spec. Returns (fields, embeds, filters, local_sort, problems)."""
    mi = SI.model_info(model) if model else None
    probs = []
    fields = ",".join(f.strip() for f in (fields or "").split(",") if f.strip())
    embeds_l = [e.strip() for e in (embeds or "").split(",") if e.strip()]
    filt = []
    if not mi:
        return fields, ",".join(embeds_l), normalize_in(expand_dates(filters or "")), (None, orderby.lstrip("-") if orderby and not orderby.startswith("-") else ""), []
    fset = set(mi["fields"]); eset = set(mi["embeds"]); flt = set(mi["filters"])
    lower_f = {f.lower(): f for f in fset}; lower_e = {e.lower(): e for e in eset}; lower_fl = {f.lower(): f for f in flt}
    fixed_fields = []
    for f in [x for x in fields.split(",") if x]:
        root = f.split(".")[0]
        if root in fset or root in eset:
            fixed_fields.append(f)
        elif root.lower() in lower_f or root.lower() in lower_e:
            fixed_fields.append((lower_f.get(root.lower()) or lower_e.get(root.lower())) + f[len(root):])
        else:
            al = {k.lower(): v for k, v in (concepts().get("field_aliases") or {}).items()}
            probs.append("field '%s' not in %s.%s Close: %s (describe_model lists fields; * marks filterable ones)" % (
                f, model, " Use: %s." % al[root.lower()] if root.lower() in al else "", ", ".join(suggest(root, fset | eset)) or "(see describe_model)"))
        if "." in f:  # dotted field needs its (deepest) embed, e.g. CurrentUnitStatus.UnitStatusType.Name -> CurrentUnitStatus.UnitStatusType
            segs = f.split(".")
            for n in range(len(segs) - 1, 0, -1):
                pref = ".".join(segs[:n])
                e = pref if pref in eset else lower_e.get(pref.lower())
                if e:
                    if e not in embeds_l and not any(x.startswith(e + ".") for x in embeds_l): embeds_l.append(e)
                    break
    fixed_emb = []
    for e in embeds_l:
        if e in eset: fixed_emb.append(e)
        elif e.lower() in lower_e: fixed_emb.append(lower_e[e.lower()])
        else: probs.append("embed '%s' not in %s. Close: %s" % (e, model, ", ".join(suggest(e, eset)) or "(none)"))
    for clause in split_top(normalize_in(expand_dates(filters or ""))):
        parts = clause.split(",", 2)
        if len(parts) != 3:
            probs.append("filter '%s' must be Field,op,value" % clause); continue
        f, op, val = parts[0].strip(), parts[1].strip().lower(), parts[2].strip()
        if f in flt and bad_filter(model, f):
            probs.append("filter '%s' does not work on %s (HTTP 404 or 0 rows). Use the sub-resource Tenants/{id}/HistoryNotes (or Units/Owners/Vendors/Properties/ServiceManagerIssues/{id}/HistoryNotes); on the HistoryNotes collection query() routes one owner id there itself." % (f, model))
            continue
        if f not in flt:
            if f.lower() in lower_fl: f = lower_fl[f.lower()]
            else:
                probs.append(filterable_hint(model, f, mi, flt))
                continue
        if op not in OPS:
            probs.append("filter op '%s' invalid. Use one of: %s" % (op, " ".join(sorted(OPS)))); continue
        if op in ("in", "ni", "bt") and not val.startswith("("): val = "(" + ",".join(split_items(val)) + ")"
        filt.append("%s,%s,%s" % (f, op, val))
    local_sort, order = None, ""
    if orderby:
        o = orderby.strip()
        name = o.lstrip("-+")
        if name.split(".")[0] not in fset and name.lower() in lower_f: name = lower_f[name.lower()]
        if name.split(".")[0] not in fset:
            probs.append("orderby '%s' not a field. Close: %s" % (name, ", ".join(suggest(name, fset))))
        if o.startswith("-"): local_sort = "-" + name   # API only sorts ascending; descending is done locally
        else: order = name
    return ",".join(fixed_fields), ",".join(fixed_emb), ";".join(filt), (local_sort, order), probs

def _env_int(name, default):
    try: return max(1, int(os.environ.get(name, "") or default))
    except ValueError: return default

MAX_ROWS = _env_int("RM_MAX_ROWS", 5000)                         # default rows per raw-row pull
MAX_ROWS_HARD = max(MAX_ROWS, _env_int("RM_MAX_ROWS_HARD", 20000))   # ceiling for an explicit max_records
OOM_LADDER = (1000, 500, 250, 100, 50)                           # page sizes tried after a server OutOfMemory
CHRONO_DEFAULT = {"transactions"}   # the API cannot sort these (HTTP 404) but returns them oldest first (verified on many ledgers)

def row_limit(max_records=0, hard=False):
    """Rows to pull: no max_records = RM_MAX_ROWS; an explicit max_records is honoured up to RM_MAX_ROWS_HARD; hard=True = the ceiling."""
    if hard: return MAX_ROWS_HARD
    try: n = int(max_records or 0)
    except (TypeError, ValueError): n = 0
    return min(n, MAX_ROWS_HARD) if n > 0 else MAX_ROWS

class Rows(list):
    """Fetched rows plus how the pull went: partial = out-of-memory message when it stopped early; newest = the newest slice was read;
    order_dropped = the API refused the sort."""
    partial = ""
    newest = False
    order_dropped = False
    chrono = False        # read in the API's own order, which is oldest first for this resource

class RMOutOfMemory(ToolError):
    def __init__(self, msg, rows=None):
        ToolError.__init__(self, msg)
        self.rows = list(rows or [])

def is_oom(s, d):
    return s == 500 and "outofmemoryexception" in (d if isinstance(d, str) else json.dumps(d)).lower()

OOM_PREFIX = "Rent Manager ran out of memory on this request (its limit, not a bug here). Narrow it: date range, property, tenant, fewer embeds. "

def oom_message(path):
    h = report_hint(path)
    return OOM_PREFIX + ("Built-in report that covers this: %s." % h if h else "Built-in report for this data: use list_reports / search_models.")

def cap_line(shown, total, kind, path, limit=None):
    """The one-line CAPPED notice (kind: newest | oldest | first). total = the API total when known."""
    tot = ("%d" % total) if total and total >= shown else "an unknown number of"
    h = report_hint(path)
    return ("CAPPED: showing the %s %d of %s rows. More exist. To see them: narrow by date range or property/tenant%s. "
            "Limit here: %d rows (default RM_MAX_ROWS); an explicit max_records raises it up to %d (RM_MAX_ROWS_HARD)."
            % (kind, shown, tot, (", or use " + h) if h else "", limit or MAX_ROWS, MAX_ROWS_HARD))

def cap_kind(rows, order=""):
    """newest | oldest | first: which end of the table a capped pull kept."""
    if getattr(rows, "newest", False): return "newest"
    if getattr(rows, "chrono", False) or (order and "date" in order.lower() and not getattr(rows, "order_dropped", False)): return "oldest"
    return "first"

def _aligned(pos, below):
    return next((x for x in OOM_LADDER if x < below and pos % x == 0), 0)

def _chunk(path, params, start, size, st):
    """Rows [start, start+size) via pages that start on a page boundary. A server OutOfMemory halves the page size (ladder
    1000/500/250/100/50) and retries; when nothing smaller is left, raises RMOutOfMemory carrying the rows already read."""
    es = max(1, min(size, st["max"]))
    out, pos, end = [], start, start + size
    while pos < end:
        if pos % es:
            es = _aligned(pos, es)
            if not es: raise RMOutOfMemory(oom_message(path), out)
        s, h, d = api(path, dict(params, pageSize=es, pageNumber=pos // es + 1))
        if s == 204: break
        if is_oom(s, d):
            st["oom"] = True
            nxt = _aligned(pos, es)
            if not nxt: raise RMOutOfMemory(oom_message(path), out)
            st["max"] = min(st["max"], nxt); es = nxt
            continue
        if s != 200: raise ToolError(api_error(s, d))
        if not isinstance(d, list):
            st["single"] = d; return []
        if st["total"] is None and h.get("x-total-results"): st["total"] = int(h["x-total-results"])
        out += d[: end - pos]
        if len(d) < es: break
        pos += es
    return out

def _pull(path, params, chunks, st, parallel, keep_partial):
    """Fetch chunks (start, size) in parallel; stop at the first out-of-memory one. -> ([(start, rows)], RMOutOfMemory or None)"""
    done, err = [], None
    ex = ThreadPoolExecutor(max(1, min(int(parallel or 4), 8)))
    try:
        futs = [ex.submit(_chunk, path, params, s, n, st) for s, n in chunks]
        for (s, n), f in zip(chunks, futs):
            try:
                done.append((s, f.result()))
            except RMOutOfMemory as e:
                err = e
                if keep_partial and e.rows: done.append((s, e.rows))
                break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return done, err

def fetch(path, params, max_records=500, page_size=0, parallel=4, on_pages=None, newest_col="", partial_ok=False):
    """Paged GET; parallel pages after the first. Returns (rows, total, pages, status); rows is a Rows (see its attributes).
    on_pages(n) is called with the page count before the remaining pages are fetched (fan-out guard).
    newest_col: a date field; when the table holds more than max_records rows, read the LAST pages of the ascending sort by that field
    so the cap keeps the NEWEST rows. A server OutOfMemory retries with smaller pages; when still failing, raises RMOutOfMemory
    (carrying rows already read) unless partial_ok, which returns them with rows.partial set."""
    orig = params
    params = {k: v for k, v in params.items() if v not in (None, "")}
    has_fields = bool(params.get("fields"))
    cap = 20000 if has_fields else 1000
    max_records = max(1, int(max_records))
    ps = int(page_size) if page_size else min(cap, max(1, max_records))
    ps = min(ps, cap)
    st = {"max": ps, "total": None, "oom": False, "single": None}
    total = None
    chrono = path.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower() in CHRONO_DEFAULT
    if chrono and params.get("orderingOptions"):
        params.pop("orderingOptions"); orig["orderingOptions"] = ""   # would only answer 404; the default order is chronological anyway
    if newest_col or (max_records > ps and on_pages):
        # big pull: size it first (1 cheap call) so the fan-out budget / the newest slice is decided BEFORE any heavy page is fetched
        try:
            s0, h0, d0 = api(path, {k: v for k, v in dict(params, pageSize=1, pageNumber=1).items() if k != "orderingOptions"}, timeout=10)
        except ToolError as e:
            if str(e).startswith("Too large to size"):
                h = report_hint(path)
                raise ToolError("%s%s" % (e, (" Built-in report that covers this: %s." % h) if h else ""))
            raise
        if s0 == 204: return Rows(), 0, 1, s0
        if is_oom(s0, d0): raise RMOutOfMemory(oom_message(path))
        if s0 != 200: raise ToolError(api_error(s0, d0))
        if isinstance(d0, list) and h0.get("x-total-results"):
            total = int(h0["x-total-results"])
            if on_pages and not (newest_col and total > max_records): on_pages(math.ceil(min(total, max_records) / ps))
    if newest_col and total is not None and total > max_records:   # the NEWEST max_records rows: last pages of the ascending date sort
        if not chrono and params.get("orderingOptions") != newest_col:
            params["orderingOptions"] = orig["orderingOptions"] = newest_col
        start0 = total - max_records
        first = (start0 // ps) * ps
        chunks = [(x, ps) for x in range(((total - 1) // ps) * ps, first - 1, -ps)]   # newest page first
        if on_pages: on_pages(len(chunks))
        done, err = _pull(path, params, chunks, st, parallel, keep_partial=False)   # a half page would leave a hole: keep whole pages only
        done.sort(key=lambda x: x[0])
        rows = Rows()
        for _, r in done: rows += r
        if done and done[0][0] < start0: del rows[: start0 - done[0][0]]
        rows.newest = True
        rows.chrono = chrono
        if err:
            if not rows or not partial_ok:
                err.rows = list(rows); raise err
            rows.partial = str(err)
        return rows, total, len(done), 200
    try:
        rows0 = _chunk(path, params, 0, ps, st)
    except RMOutOfMemory as e:
        if not (e.rows and partial_ok): raise
        r0 = Rows(e.rows); r0.partial = str(e)
        return r0, st["total"] or len(e.rows), 1, 200
    if st["single"] is not None: return st["single"], 1, 1, 200
    if not rows0 and not st["total"]: return Rows(), 0, 1, 204
    total = st["total"] if st["total"] is not None else (total if total is not None else len(rows0))
    rows = Rows(rows0)
    want = min(total, max_records)
    pages = math.ceil(want / ps)
    if on_pages: on_pages(pages)
    if pages > 1 and len(rows0) >= ps:
        done, err = _pull(path, params, [(x * ps, ps) for x in range(1, pages)], st, parallel, keep_partial=True)
        for _, r in done: rows += r
        if err:
            if not partial_ok:
                err.rows = list(rows); raise err
            rows.partial = str(err)
    del rows[max_records:]
    rows.chrono = chrono
    return rows, total, pages, 200

def fetch_ordered(path, params, max_records=500, page_size=0, parallel=4, on_pages=None, newest_col="", partial_ok=False):
    """fetch(); if the API rejects orderingOptions for this resource (404 KeyNotFound), retry unsorted.
    Clears params['orderingOptions'] so the caller sorts locally."""
    try:
        return fetch(path, params, max_records, page_size, parallel, on_pages, newest_col, partial_ok)
    except ToolError as e:
        if params.get("orderingOptions") and "given key was not present" in str(e):
            params["orderingOptions"] = ""
            rows, total, pages, s = fetch(path, params, max_records, page_size, parallel, on_pages, "", partial_ok)
            if isinstance(rows, Rows): rows.order_dropped = True
            return rows, total, pages, s
        if "given key was not present" in str(e):
            raise ToolError("%s | %s" % (e, diagnose_key_error(path, params)))
        raise

def diagnose_key_error(path, params):
    """HTTP 404 'given key was not present': retry with ONE part removed at a time (pageSize 1, at most 4 calls) and say which part
    makes the call work, plus what was sent. Never raises."""
    sent = ", ".join("%s=%s" % (k, str(params[k])[:120]) for k in ("filters", "embeds", "fields", "orderingOptions") if params.get(k))
    try:
        for key, label in (("embeds", "an embed"), ("filters", "a filter"), ("fields", "a field"), ("orderingOptions", "the orderby")):
            if not params.get(key): continue
            p = {k: v for k, v in params.items() if k not in (key, "pageNumber")}
            if key == "embeds": p.pop("fields", None)   # fields may reference the embed
            p["pageSize"] = 1
            s, h, d = api(path, p, timeout=15)
            if s in (200, 204):
                return "Retrying without %s works, so %s is the likely cause (sent: %s). Nested filters such as Tenant.TenantID often fail on collections: use the sub-resource (e.g. Tenants/{id}/HistoryNotes)." % (
                    key if key != "orderingOptions" else "orderby", label, sent)
    except Exception:
        pass
    return "Could not tell which part is bad (sent: %s). Try fewer embeds/fields, top-level filters only, no orderby." % (sent or "nothing")

FANOUT_BUDGET = int(os.environ.get("RM_FANOUT_BUDGET", "50"))
_fanout = {"ids": set(), "lock": threading.Lock()}

def check_fanout(r, pages, allow):
    """Refuse per-record fan-out (many calls for different IDs) and huge page counts beyond the call budget."""
    msg = ("Use ONE built-in report (run_report / a recipe; covers all properties) or ONE filtered bulk query "
           "(e.g. Tenants/Transactions or Charges with PropertyID/date filters + fields) instead of many calls. "
           "If the user really wants this, call again with allow_fanout=true.")
    if pages > FANOUT_BUDGET and not allow:
        raise ToolError("This pull needs %d pages (> budget %d). Add filters/fields (fields allow 20000-row pages) or a smaller max_records. %s"
                        % (pages, FANOUT_BUDGET, msg))
    if r.get("id"):
        with _fanout["lock"]:
            if time.time() - _fanout.get("t0", 0) > 600:  # budget window resets every 10 min
                _fanout["ids"].clear(); _fanout["t0"] = time.time()
            _fanout["ids"].add((r["ctrl"], r["id"]))
            n = len(_fanout["ids"])
        if n > FANOUT_BUDGET and not allow:
            raise ToolError("Per-record fan-out budget reached (%d different %s/{id} calls this session, budget %d). %s"
                            % (n, r["ctrl"], FANOUT_BUDGET, msg))

_ID_SCOPE = re.compile(r"(?:^|;)\s*((?:Unit|Tenant|Lease|LeaseRenewal|Owner|Vendor|EntityKey|ParentLease)ID|Unit\.UnitID|Tenant\.TenantID)\s*,\s*(eq|in)\s*,\s*\(?([\d,\s]+)\)?", re.I)

def scope_from_filters(filters):
    m = re.search(r"(?:^|;)\s*PropertyID\s*,\s*(eq|in)\s*,\s*\(?([\d,\s]+)\)?", filters or "", re.I)
    if not m:
        m2 = _ID_SCOPE.search(filters or "")
        if not m2: return None
        ids = re.findall(r"\d+", m2.group(3))
        shown = ",".join(ids[:8]) + (",... (%d ids)" % len(ids) if len(ids) > 8 else "")
        return "filtered by %s in (%s) (not the whole portfolio)" % (m2.group(1), shown)
    ids = [int(x) for x in re.findall(r"\d+", m.group(2))]
    try:
        names = {p["id"]: p["name"] for p in properties()}
    except ToolError:
        names = {}
    return "Property: " + ", ".join("%s (ID %d)" % (names.get(i, "?"), i) for i in ids)

# ----------------------------------------------------------------------------- tools
def t_search_models(query, limit=8):
    i = idx()
    hits = SI.search(query, limit=int(limit))
    lines = concept_lines(query) + suggestion_lines(query)
    recs = recipe_search(query)[:3]
    if recs: lines.append("Recipes (fastest, try first): " + ", ".join(r["name"] for _, r in recs))
    reps = SI.search(query, "reports", limit=4)
    if reps:
        names = {r["id"]: r["name"] for r in i["reports"]}
        lines.append("Built-in reports (one call, all properties): " + ", ".join("%s (id %s)" % (names[x], x) for _, x in reps))
    lines.append("Resources for '%s' (for ONE filtered bulk query; describe_model next):" % query)
    for sc, n in hits:
        r = i["resources"][n]
        mi = SI.model_info(r["model"]) if r.get("model") else None
        st = r.get("status")
        note = "" if st in (None, 200) else (" [no rows/privilege: %s]" % st if st == 204 else " [NOT READABLE: HTTP %s]" % st)
        lines.append("- %s (model %s, %d fields, %d sub-resources)%s%s" % (
            n, r.get("model"), len(mi["fields"]) if mi else 0, len(r.get("subs") or {}),
            "" if r.get("list") else " [by ID only]", note))
    if not hits: lines.append("(no matches; try other words)")
    return "\n".join(lines)

def t_describe_model(model, section="summary", contains="", limit=150):
    i = idx()
    r = {}
    try:
        r = find_resource(model); mname = r["model"]; path = r["path"]
    except ToolError:
        mname = model[:-5] if model.endswith("Model") else model
        path = None
        if not SI.model_info(mname):
            raise
    mi = SI.model_info(mname)
    if not mi: raise ToolError("No spec for model '%s'." % mname)
    c = (contains or "").lower()
    sec = (section or "summary").lower()
    eset = set(mi["embeds"])
    fset_f = set(mi["filters"])
    if sec == "fields":
        items = ["%s%s:%s" % (k, "*" if k in fset_f else "", v) for k, v in mi["fields"].items() if c in k.lower()]
        return "%s fields (%d)%s (* = filterable):\n%s" % (mname, len(items), " containing '%s'" % c if c else "", ", ".join(items[:int(limit)]))
    if sec == "filters":
        allf = [f for f in mi["filters"] if c in f.lower() and not bad_filter(mname, f)]
        items = allf if c else [f for f in allf if "." not in f]
        items.sort(key=lambda x: (x.count("."), x))
        hidden = len(allf) - len(items)
        return "%s filters (%d%s%s):\n%s%s%s" % (mname, len(items), " containing '%s'" % c if c else ", top-level", "; nested ones use dots" if c else "",
                ", ".join(items[:int(limit)]), "\n...more, pass contains=" if len(items) > int(limit) else "",
                "\n(%d nested filters such as Property.Name hidden: pass contains='Property.' (a prefix or any text) to list them)" % hidden if hidden else "")
    if sec == "embeds":
        items = [e for e in mi["embeds"] if c in e.lower()]
        items.sort(key=lambda x: (x.count("."), x))
        return "%s embeds (%d):\n%s" % (mname, len(items), ", ".join(items[:int(limit)]))
    if sec in ("subs", "subresources", "sub-resources"):
        ctrl = path.strip("/").split("/")[0] if path else None
        subs = (i["resources"].get(ctrl) or {}).get("subs") or {}
        lines = ["%s sub-resources (GET):" % ctrl]
        for k, v in subs.items():
            if c and c not in k.lower(): continue
            p = []
            if v.get("by_id"): p.append("%s/{id}/%s" % (ctrl, k))
            if v.get("all"): p.append("%s/%s" % (ctrl, k))
            lines.append("- %s -> %s%s" % (" | ".join(p), v.get("model"), " params:" + ",".join(v["params"]) if v.get("params") else ""))
        return "\n".join(lines)
    # summary
    scalar = [k for k in mi["fields"] if k not in eset]
    enums = ["%s:%s" % (k, v) for k, v in mi["fields"].items() if re.match(r"^e[A-Z]", str(v))]
    top_f = [f for f in mi["filters"] if "." not in f and not bad_filter(mname, f)]
    top_e = [e for e in mi["embeds"] if "." not in e]
    ctrl = path.strip("/").split("/")[0] if path else None
    subs = list(((i["resources"].get(ctrl) or {}).get("subs") or {}).keys()) if ctrl and "/" not in path.strip("/") else []
    st = (i["resources"].get(ctrl) or {}).get("status") if ctrl else None
    out = ["%s -> model %s%s" % (path or "(no direct endpoint)", mname,
                                 "" if st in (None, 200) else "  [live check: HTTP %s%s]" % (st, " = no rows or no privilege" if st == 204 else "")),
           "fields (%d; * = filterable, the rest only via fields= or local where=): %s" % (len(scalar), ", ".join(k + ("*" if k in fset_f else "") for k in scalar[:80]) + (" ..." if len(scalar) > 80 else "")),
           "filters (top-level %d of %d): %s" % (len(top_f), len(mi["filters"]), ", ".join(top_f[:70]) + (" ..." if len(top_f) > 70 else "")),
           "embeds (%d): %s" % (len(top_e), ", ".join(top_e[:60]) + (" ..." if len(top_e) > 60 else ""))]
    if enums: out.append("enum fields (see enum_values): " + ", ".join(enums[:20]))
    if subs: out.append("sub-resources: %s (section=subs for paths)" % ", ".join(subs[:50]))
    wrong = [(k, v) for k, v in (concepts().get("field_aliases") or {}).items()
             if k not in mi["fields"] and k not in eset and k not in fset_f and v.split(" ")[0].split(".")[0] in (set(mi["fields"]) | eset | fset_f)]
    if wrong: out.append("Common wrong names -> use: " + "; ".join("%s -> %s" % kv for kv in wrong))
    out += model_notes([mname, ctrl, r.get("sub")])
    out.append("Batch with in(): filters 'UnitID,in,(1,2,3)' (comma list in parentheses; ';' separates clauses) instead of one call per unit/tenant.")
    out.append("Next: query(resource='%s', fields='...', filters='Field,op,value')" % (path.strip("/") if path else mname))
    return "\n".join(out)

def t_enum_values(name):
    p = os.path.join(SI.SPEC, "enums", name + ".json")
    if not os.path.exists(p):
        names = idx().get("enums") or []
        low = {n.lower(): n for n in names}
        if name.lower() in low: p = os.path.join(SI.SPEC, "enums", low[name.lower()] + ".json")
        else: raise ToolError("Unknown enum '%s'. Close: %s" % (name, ", ".join(suggest(name, names, 8))))
    d = json.load(open(p, encoding="utf-8"))
    return "%s:\n%s" % (os.path.basename(p)[:-5], "\n".join("%s = %s%s" % (e.get("Value"), e.get("Name"), " (%s)" % e["Description"] if e.get("Description") and e["Description"] != e.get("Name") else "") for e in d))

def t_count(resource, filters=""):
    t0 = time.time()
    r = find_resource(resource)
    if r.get("sub_all") and not filters:
        raise ToolError("%s across all records is very slow (can time out). Add filters or use %s/{id}/%s." % (r["path"], r["ctrl"], r["sub"]))
    _, _, flt, _, probs = validate(r["model"], filters=filters)
    if probs: raise ToolError("Fix and retry:\n- " + "\n- ".join(probs))
    mi = SI.model_info(r["model"]) or {"fields": {}}
    key = next(iter(mi["fields"]), None)
    s, h, d = api(r["path"], {"filters": flt, "pageSize": 1, **({"fields": key} if key else {})}, timeout=10)
    if s == 204: n = 0
    elif s != 200: raise ToolError(api_error(s, d))
    else: n = int(h.get("x-total-results") or (len(d) if isinstance(d, list) else 1))
    scope = scope_from_filters(flt) or all_scope_label()
    return "count %s%s = %d\nScope: %s\n(%.1fs, live)" % (r["path"], " where " + flt if flt else "", n, scope, time.time() - t0)

def _d10(v):
    return str(v or "")[:10]

def summarize_rent(flat, as_of="", status=None, info=None):
    """Recurring-charge rows (EntityKeyID, UnitID, Amount, FromDate, ToDate, ChargeType.Name/Description) -> one row per tenant.
    Rent = tenant-paid rent types + subsidy types (rental assistance, HAP...); both are rent. Effective date per tenant: as_of if
    given, else today when any rent charge is open-ended/current, else the end date of the tenant's last rent charge.
    Tenants whose charges match no rent type, or that have no charge in effect on as_of, are still listed (note column).
    status = {TenantID: 'Past'|'Current'...}: an open-ended rent charge of a Past tenant is flagged STALE.
    info (dict, optional) receives not_counted = [(code, description, n_tenants)] and stale = count."""
    today = _today().isoformat()
    ref_in = parse_date(as_of) if as_of else ""
    by, other = {}, {}
    not_counted = {}
    for r in flat:
        key = (r.get("EntityKeyID"), r.get("UnitID"))
        fam = charge_family(r.get("ChargeType.Name"), r.get("ChargeType.Description"))
        if fam: by.setdefault(key, []).append((fam, r))
        else:
            other.setdefault(key, []).append(r)
            not_counted.setdefault((str(r.get("ChargeType.Name") or ""), str(r.get("ChargeType.Description") or "")), set()).add(r.get("EntityKeyID"))
    out, stale = [], 0
    for key in list(by) + [k for k in other if k not in by]:
        tid, uid = key
        items = by.get(key, [])
        oth = other.get(key, [])
        row = {"TenantID": tid, "UnitID": uid, "rent_as_of": ref_in, "tenant_portion": 0.0, "subsidy_portion": 0.0, "total_rent": 0.0,
               "charge_types": "", "since": ""}
        note = ""
        if items:
            ref = ref_in
            if not ref:
                ends = [_d10(r.get("ToDate")) for _, r in items]
                ref = today if any(e == "" or e >= today for e in ends) else max(ends)
            eff = [(f, r) for f, r in items if _d10(r.get("FromDate")) <= ref and (not _d10(r.get("ToDate")) or _d10(r.get("ToDate")) >= ref)]
            row["rent_as_of"] = ref
            if eff:
                tenant = round(sum(_num(r.get("Amount")) or 0.0 for f, r in eff if f == "rent"), 2)
                sub = round(sum(_num(r.get("Amount")) or 0.0 for f, r in eff if f == "subsidy"), 2)
                row.update(tenant_portion=tenant, subsidy_portion=sub, total_rent=round(tenant + sub, 2),
                           charge_types=" + ".join("%s %s%s" % (r.get("ChargeType.Name"), "%.2f" % (_num(r.get("Amount")) or 0.0), " (subsidy)" if f == "subsidy" else "") for f, r in eff),
                           since=min(_d10(r.get("FromDate")) for _, r in eff))
                if status is not None and str(status.get(str(tid)) or status.get(tid) or "").lower() == "past" and any(not _d10(r.get("ToDate")) for _, r in eff):
                    note = "STALE? tenant moved out (status Past) but this rent charge is open-ended"; stale += 1
            else:
                note = "no rent charge in effect on %s" % ref
        else:
            row["rent_as_of"] = ref_in or ""
            note = "no rent-type charge; other: " + ", ".join("%s %.2f" % (r.get("ChargeType.Name"), _num(r.get("Amount")) or 0.0) for r in oth[:4])
        if status is not None: row["tenant_status"] = status.get(str(tid)) or status.get(tid) or ""
        row["note"] = note
        out.append(row)
    if info is not None:
        info["not_counted"] = sorted(((c, d, len(t)) for (c, d), t in not_counted.items()), key=lambda x: -x[2])
        info["stale"] = stale
    return out

def rent_type_label(description, transaction_type="Charge"):
    """Ledger row label: 'rent (tenant portion)' / 'rent (subsidy portion)' for rent-family charge rows, else ''."""
    fam = charge_family(description, description) if str(transaction_type) == "Charge" else None
    return {"rent": "rent (tenant portion)", "subsidy": "rent (subsidy portion)"}.get(fam, "")

def summarize_ledger(flat):
    """Ledger rows -> ONE summary row: last rent charge (+ subsidy portion, same date), last payment, balance."""
    def fam(r): return charge_family(r.get("Description"), r.get("Description")) if r.get("TransactionType") == "Charge" else None
    rent_rows = [r for r in flat if fam(r)]
    last = max((_d10(r.get("TransactionDate")) for r in rent_rows), default="")
    same = [r for r in rent_rows if _d10(r.get("TransactionDate")) == last]
    tenant = round(sum(_num(r.get("Amount")) or 0.0 for r in same if fam(r) == "rent"), 2)
    sub = round(sum(_num(r.get("Amount")) or 0.0 for r in same if fam(r) == "subsidy"), 2)
    pays = [r for r in flat if r.get("TransactionType") == "Payment"]
    lp = max(pays, key=lambda r: _d10(r.get("TransactionDate")), default=None)
    return [{"last_rent_charge_date": last, "tenant_portion": tenant, "subsidy_portion": sub, "total_rent": round(tenant + sub, 2),
             "charge_types": " + ".join("%s %.2f" % (r.get("Description"), _num(r.get("Amount")) or 0.0) for r in same),
             "last_payment_date": _d10(lp.get("TransactionDate")) if lp else "", "last_payment_amount": (_num(lp.get("Amount")) if lp else None),
             "last_payment_from_subsidy": bool(lp and lp.get("SubsidyID") not in (None, -1, "-1")) if lp else None,
             "balance": round(sum(_num(r.get("Amount")) or 0.0 for r in flat), 2), "rows": len(flat)}]

def tenant_statuses(ids, max_calls=25):
    """{str(TenantID): Status} for the tenants in ids (batched in()); None when it cannot be determined (failure or too many)."""
    ids = sorted({str(i) for i in ids if i not in (None, "")}, key=lambda x: (len(x), x))
    if not ids: return {}
    if len(ids) > 200 * max_calls: return None
    out = {}
    try:
        for i in range(0, len(ids), 200):
            s, h, d = api("/Tenants", {"pageSize": 1000, "fields": "TenantID,Status", "filters": "TenantID,in,(%s)" % ",".join(ids[i:i + 200])})
            if s == 200 and isinstance(d, list):
                for t in d: out[str(t.get("TenantID"))] = t.get("Status")
            elif s != 204: return None
    except Exception:
        return None
    return out

def _model_date_fields(model):
    mi = SI.model_info(model) if model else None
    return [n for n in ((mi or {}).get("fields") or {}) if str(n).lower().endswith("date") and "." not in str(n)]

def _date_col(fl, orderby=""):
    """Column to order newest-first: the orderby field if it is a date, else the first *Date field requested."""
    cols = [x for x in (fl or "").split(",") if x]
    ob = (orderby or "").lstrip("-+")
    if ob and "date" in ob.lower(): return ob
    for pref in ("TransactionDate", "MoveOutDate", "PostDate", "FromDate", "StartDate"):
        if pref in cols: return pref
    return next((c for c in cols if c.lower().endswith("date")), "")

_NESTED_HIST = {"tenant.tenantid": "Tenants", "unit.unitid": "Units", "owner.ownerid": "Owners", "vendor.vendorid": "Vendors",
                "property.propertyid": "Properties", "servicemanagerissue.servicemanagerissueid": "ServiceManagerIssues"}

def route_history_filters(filters):
    """HistoryNotes collection + a nested owner filter (Tenant.TenantID eq|in ...) -> (parent resource, [ids], remaining filters) or None.
    The API answers those filters with 404 / 0 rows; the sub-resource <Parent>/{id}/HistoryNotes works."""
    f = normalize_in(expand_dates(filters or ""))
    keep, hit = [], None
    for clause in split_top(f):
        parts = clause.split(",", 2)
        k = parts[0].strip().lower()
        if k in _NESTED_HIST:
            if len(parts) != 3 or parts[1].strip().lower() not in ("eq", "in") or hit:
                raise ToolError("Filter '%s' does not work on the HistoryNotes collection. Use eq/in on ONE of %s, or the sub-resource directly: Tenants/{id}/HistoryNotes." % (clause, ", ".join(_NESTED_HIST)))
            hit = (_NESTED_HIST[k], [int(x) for x in re.findall(r"\d+", parts[2])])
        elif clause.strip():
            keep.append(clause)
    if not hit: return None
    if not hit[1]: raise ToolError("No ids in the %s filter." % hit[0])
    return hit[0], hit[1], ";".join(keep)

def t_query(resource, filters="", **kw):
    """Routes known-broken nested HistoryNotes filters to the working sub-resource, else _query_one."""
    try:
        r0 = find_resource(resource)
    except ToolError:
        r0 = None
    if r0 and r0["ctrl"] == "HistoryNotes" and not r0["id"] and not r0.get("sub"):
        routed = route_history_filters(filters)
        if routed:
            parent, ids, rest = routed
            if len(ids) > 25: raise ToolError("Routing by %s needs one call per id; at most 25 ids (got %d). Narrow the ids." % (parent, len(ids)))
            outs = []
            for i in ids:
                kw2 = dict(kw)
                kw2["save_as"] = kw.get("save_as") or "%s_%d_HistoryNotes" % (parent, i)
                outs.append(_query_one("%s/%d/HistoryNotes" % (parent, i), rest, **kw2))
            return lift_notice(("Routed: nested filters on the HistoryNotes collection fail in the API, so %d call(s) to %s/{id}/HistoryNotes were used instead.\n"
                                % (len(ids), parent)) + "\n----\n".join(outs))
    return _query_one(resource, filters, **kw)

def _query_one(resource, filters="", fields="", embeds="", orderby="", page_size=0, max_records=0, parallel=4,
            columns="", group_by="", sum="", sort="", top=0, where="", show=0, save_as="", allow_fanout=False,
            most_recent_first=False, first_per="", summary="", as_of="", _scope=None):
    t0 = time.time()
    r = find_resource(resource)
    if r.get("model") == "Lease" and not r.get("sub") and not fields and not summary and not group_by and not columns:
        # v11: the API's default Lease output has no StartDate/EndDate; ask for the useful columns (plus any embedded objects)
        roots = []
        for e in [x.strip() for x in (embeds or "").split(",") if x.strip()]:
            if e.split(".")[0] not in roots: roots.append(e.split(".")[0])
        fields = ",".join(["LeaseID", "TenantID", "UnitID", "PropertyID", "StartDate", "EndDate", "MoveInDate", "MoveOutDate", "NoticeDate", "ExpectedMoveOutDate"] + roots)
    check_fanout(r, 0, allow_fanout)
    if r.get("sub_all") and not filters:
        raise ToolError("%s across ALL records is very slow and can fail. Add filters (e.g. PropertyID,eq,N or a date range) or use %s/{id}/%s." % (r["path"], r["ctrl"], r["sub"]))
    summary = str(summary or "").strip().lower()
    hard_limit = False   # summaries need every source row (small tables): read up to RM_MAX_ROWS_HARD, not RM_MAX_ROWS
    if summary and summary not in ("rent", "ledger"):
        raise ToolError("summary must be 'rent' (on RecurringCharges: tenant + subsidy portions and total per tenant) or 'ledger' (on Tenants/{id}/Transactions: last rent charge, last payment, balance).")
    if summary == "rent":
        if r["model"] != "RecurringCharge":
            raise ToolError("summary='rent' works on RecurringCharges, e.g. query(resource='RecurringCharges', filters='UnitID,in,(1,2,3)', summary='rent').")
        fields, embeds = "EntityKeyID,EntityType,UnitID,Amount,FromDate,ToDate,ChargeType.Name,ChargeType.Description", "ChargeType"
        if "EntityType" not in (filters or ""): filters = (filters + ";" if filters else "") + "EntityType,eq,Tenant"
        hard_limit = True   # a property-wide summary must never be silently cut short
    elif summary == "ledger":
        if r["model"] != "Transaction":
            raise ToolError("summary='ledger' works on one tenant's ledger: query(resource='Tenants/{id}/Transactions', summary='ledger').")
        fields, embeds = "TransactionDate,TransactionType,Description,Amount,SubsidyID,UnitID", ""
        hard_limit = True
    fl, em, flt, (lsort, order), probs = validate(r["model"], fields, embeds, filters, orderby)
    if probs:
        raise ToolError("Nothing was run. Fix these and retry:\n- " + "\n- ".join(probs))
    params = {"fields": fl, "embeds": em, "filters": flt, "orderingOptions": order}
    max_records = row_limit(max_records, hard_limit)
    mrf_col = ""
    mrf_ignored = ""
    if most_recent_first and not summary and (sort or group_by or lsort):
        mrf_ignored = "most_recent_first ignored: %s given" % ("sort" if sort else "group_by" if group_by else "a descending orderby")
        most_recent_first = False
    if most_recent_first and not summary:
        mrf_col = _date_col(fl, orderby)
        if not mrf_col: raise ToolError("most_recent_first needs a date column: add one to fields (e.g. TransactionDate) or pass orderby='-TransactionDate'.")
        if mrf_col not in [x for x in fl.split(",") if x]: fl = params["fields"] = (fl + "," if fl else "") + mrf_col
    # newest rows win the cap: when more rows exist than the cap, read the LAST pages of the ascending date sort (unless the user asked
    # for another order, an aggregate or oldest-first)
    newest_col = ""
    if not summary and not first_per and not (sort or group_by):
        if lsort and "date" in lsort.lower() and not order:   # orderby='-SomeDate': the user wants the newest first
            newest_col = lsort.lstrip("-")
        elif not lsort and (not order or order == mrf_col):
            newest_col = mrf_col or ("" if order else _date_col(fl or ",".join(_model_date_fields(r.get("model"))), ""))
        if newest_col and fl and newest_col not in [x for x in fl.split(",") if x]: newest_col = ""
    try:
        rows, total, pages, s = fetch_ordered(r["path"], params, max_records, page_size, parallel,
                                              on_pages=lambda n: check_fanout({}, n, allow_fanout), newest_col=newest_col, partial_ok=True)
    except ToolError as e:
        q = (concepts().get("filter_quirks") or {}).get(r["ctrl"]) or (concepts().get("filter_quirks") or {}).get(r.get("sub") or "")
        if "Unable to locate target property" in str(e) and q: raise ToolError("%s | Filter note: %s" % (e, q))
        raise
    if order and not params.get("orderingOptions"): sort = sort or order  # API could not sort this resource
    raw = rows
    rows_obj = rows
    if isinstance(rows, dict) or not isinstance(rows, list):
        rows = [rows] if isinstance(rows, dict) else [{"value": rows}]
    nrec = len(rows)
    flat = flatten_rows(rows)
    if len(flat) != nrec: pre_explode = "%d row(s) spread over %d lines: one line per child of a one-to-many embed, parent columns repeated" % (nrec, len(flat))
    else: pre_explode = ""
    for f in flat:  # requested fields always present as columns (embeds can come back empty)
        for k in [x for x in fl.split(",") if x]: f.setdefault(k, None)
    if lsort: sort = sort or lsort
    pre_notes = [mrf_ignored] if mrf_ignored else []
    if pre_explode: pre_notes.append(pre_explode)
    lead = ""
    got_newest = bool(getattr(rows_obj, "newest", False))
    if got_newest and not mrf_col and newest_col and flat and newest_col in flat[0]: mrf_col = newest_col   # the newest slice is shown newest first
    if getattr(rows_obj, "partial", ""):
        lead = "PARTIAL: only the first %d of %s rows were read before Rent Manager stopped. %s\n" % (nrec, total if total >= nrec else "an unknown number of", rows_obj.partial)
    elif total > nrec:
        kind = cap_kind(rows_obj, order)
        lead = cap_line(nrec, total, kind, r["path"], max_records) + "\n"
        if kind == "first" and (mrf_col or lsort or newest_col):
            pre_notes.append("the newest rows could not be selected (the API cannot sort this resource); add filters (property/date) to reach them")
    if summary:
        sinfo = {}
        if summary == "rent":
            sts = tenant_statuses({f.get("EntityKeyID") for f in flat})
            summ = summarize_rent(flat, as_of, sts, sinfo)
        else:
            summ = summarize_ledger(flat)
        shown, tot, notes = post_process(summ, columns, None, group_by, sum, sort, top, where, first_per=first_per or None)
        notes.insert(0, "summary=%s of %d row(s)%s" % (summary, len(flat), "; total_rent = tenant_portion + subsidy_portion (subsidy IS rent)" if summary == "rent" else ""))
        if total > nrec: notes.insert(1, "PARTIAL: only %d of %d source rows were read, so the summary is incomplete (add filters)" % (nrec, total))
        if summary == "rent":
            if sinfo.get("not_counted"):
                notes.append("NOT counted as rent: " + ", ".join("%s%s (%d tenants)" % (c, " " + d if d and d != c else "", n) for c, d, n in sinfo["not_counted"][:12])
                             + " (edit local/charge_types.json to change)")
            if sts is None: notes.append("tenant status could not be checked (moved-out tenants with open-ended charges are not flagged)")
            if sinfo.get("stale"): notes.append("%d tenant(s) already moved out still have an OPEN-ENDED rent charge (note column); their rent may be stale" % sinfo["stale"])
        jp, cp = save(save_as or r["path"].strip("/").replace("/", "_") + "_summary", raw, summ)
    else:
        shown, tot, notes = post_process(flat, columns, None, group_by, sum, sort, top, where, first_per=first_per or None,
                                         newest_first=mrf_col if (mrf_col and not sort and not group_by) else None)
        jp, cp = save(save_as or r["path"].strip("/").replace("/", "_"), raw, flat)
    if (first_per or lsort) and not mrf_col and not summary and total > nrec and not got_newest:
        pre_notes.append("INCOMPLETE: %s applied to only %d of %d rows (max_records cap): raise max_records or add filters" % ("first_per" if first_per else "descending sort", nrec, total))
    notes = pre_notes + notes
    scope = _scope or scope_from_filters(flt) or ("record %s" % r["id"] if r["id"] else all_scope_label())
    head = "%s: %d of %d record(s)%s. Scope: %s" % (
        r["path"], nrec, total, "; " + "; ".join(notes) if notes else "", scope)
    if r.get("status") == 204 and not flat:
        head += "\nNote: this resource returned no rows at index time too; the API user may lack the privilege or property access."
    hint = "" if fl or group_by or columns or len(flat) == 0 or summary else "\nTip: pass fields='A,B,C' to limit columns (faster, larger pages)."
    pre = "%s%s\n" % (lead, head)
    lim, large = show_plan(show, len(shown))
    body, nshown = fit_table(shown, lim, tot, MAX_TEXT - INLINE_RESERVE - len(pre) - len(hint))
    more = ""
    if nshown < len(shown):
        dc = _date_col(fl, orderby)
        if dc and not (mrf_col or sort or summary):
            more = "\n(rows are in the API's order, not newest first; for the newest first add most_recent_first=true or sort='-%s')" % dc
    return clip("%s%s%s\n%s\n(%.1fs, %d page(s), live)%s" % (pre, body, more, deliver(shown, nshown, jp, cp, summ if summary else flat, large),
                                                             time.time() - t0, pages, hint))

# ----------------------------------------------------------------------------- reports
def report_by(id_or_name):
    i = idx()
    key = str(id_or_name).strip()
    for r in i["reports"]:
        if r["id"] == key or r["name"].lower() == key.lower() or r["name"].lower().replace(" ", "") == key.lower().replace(" ", ""):
            return r
    hits = [x for _, x in SI.search(key, "reports", 5)]
    names = {r["id"]: r["name"] for r in i["reports"]}
    raise ToolError("Unknown report '%s'. Close: %s" % (key, ", ".join("%s (id %s)" % (names[h], h) for h in hits) or "use list_reports"))

def recipe_report(req):
    """Report named by name (portable across instances); report_id is only a hint, doctor warns on mismatch."""
    return report_by(req["report"])["id"]

def _param_summary(rep, maxn=6):
    """One-line parameter list for list_reports: Key=default, visible keys only, property scope key first."""
    ps = [p for p in rep.get("params") or [] if p.get("key") and not p.get("hidden")]
    ps.sort(key=lambda p: 0 if p.get("ids_of") == "Property" else 1)
    out = ["%s=%s" % (p["key"], p.get("default") if p.get("default") not in (None, "") else "") for p in ps[:maxn]]
    return ", ".join(out) + (", +%d more" % (len(ps) - maxn) if len(ps) > maxn else "")

def _ready_call(rep):
    """Copy-ready run_report line: first non-scope parameter with its default as the example Key,Value."""
    ex = next((p for p in rep.get("params") or [] if p.get("key") and not p.get("hidden") and not p.get("ids_of")), None)
    par = "%s,%s" % (ex["key"], ex.get("default") if ex.get("default") not in (None, "") else "") if ex else ""
    return "run_report(id=%s, parameters='%s'%s)" % (rep["id"], par, ", property='...'" if _prop_param(rep) else "")

def t_list_reports(search="", limit=20):
    i = idx()
    by_id = {r["id"]: r for r in i["reports"]}
    if search:
        ids = [h for _, h in SI.search(search, "reports", int(limit))]
    else:
        ids = [r["id"] for r in i["reports"]][: int(limit)]
    rows = []
    for n, h in enumerate(ids):
        rep = by_id[h]
        rows.append("%s  %s" % (h, rep["name"]) + ("  [%s]" % _param_summary(rep) if rep.get("params") else ""))
    calls = [_ready_call(by_id[h]) for h in ids[:3]] if search else []
    tail = "Next: run_report(id, parameters='Key,Value;...') (report_info(id) for every option and allowed values)."
    if calls: tail = "Ready to run (fill Key,Value from the [params]; omit a key for its default):\n" + "\n".join(calls) + "\n" + tail
    return "Reports (id  name  [parameters = defaults])%s:\n%s\n%s" % (" for '%s'" % search if search else "", "\n".join(rows) or "(none)", tail)

def _prop_param(rep):
    for p in rep.get("params") or []:
        if p.get("ids_of") == "Property": return p["key"]
    return None

def t_report_info(id):
    rep = report_by(id)
    lines = ["Report %s %s. Parameters (key: type = default [allowed]); omit a key to use its default:" % (rep["id"], rep["name"])]
    for p in rep.get("params") or []:
        if p.get("hidden"): continue
        extra = []
        if p.get("values"): extra.append("values " + "/".join(str(v) for v in p["values"][:12]))
        if p.get("enum"): extra.append("enum_values('%s')%s" % (p["enum"], " minus " + str(p["exclude"]) if p.get("exclude") else ""))
        if p.get("ids_of"): extra.append("IDs of %s%s" % (p["ids_of"], ", list like (1,2)" if p.get("multi") else ""))
        lines.append("- %s: %s = %s  %s %s" % (p["key"], p.get("type"), p.get("default"), p.get("label", ""), "[" + "; ".join(extra) + "]" if extra else ""))
    pk = _prop_param(rep)
    if pk: lines.append("Property scope key: %s (run_report also accepts property='name or ID')." % pk)
    lines.append("Dates: YYYY-MM-DD. Example: run_report(id=%s, parameters='Key,Value;Key2,Value2', property='...')" % rep["id"])
    return "\n".join(lines)

def build_report_params(rep, parameters):
    """parameters: 'K,V;K2,V2' or dict. Validates keys case-insensitively; returns ordered dict."""
    if isinstance(parameters, dict): items = list(parameters.items())
    else:
        items = []
        for clause in split_top(parameters or ""):
            if "," not in clause: raise ToolError("parameter '%s' must be Key,Value" % clause)
            k, v = clause.split(",", 1); items.append((k.strip(), v.strip()))
    known = {p["key"].lower(): p for p in rep.get("params") or [] if p.get("key")}
    out, probs = {}, []
    for k, v in items:
        p = known.get(str(k).lower())
        if not p:
            probs.append("'%s' is not a parameter of %s. Close: %s. e.g. parameters='%s' (all keys: report_info(%s))" % (k, rep["name"], ", ".join(suggest(k, [x["key"] for x in known.values()])) or "(see report_info)", ",".join(next(([x["key"], str(x.get("default") or "")] for x in known.values() if x["key"].lower() not in ("propertyids", "propownerids")), ["Key", "Value"])), rep["id"])); continue
        v = "" if v is None else v
        if isinstance(v, list): v = "(" + ",".join(str(x) for x in v) + ")"
        v = str(v)
        if str(p.get("type")).lower() == "date" and v and " - " not in v and v.lower() != "all": v = parse_date(v)
        elif " - " in v:  # date ranges only parse as MM/DD/YYYY - MM/DD/YYYY (verified live)
            v = " - ".join(datetime.date.fromisoformat(parse_date(x)).strftime("%m/%d/%Y") for x in v.split(" - "))
        if isinstance(v, str) and v.lower() in ("true", "false") and str(p.get("type")).lower() == "bool": v = v.capitalize()
        if p.get("multi") and v and not v.startswith("(") and v.lower() != "all": v = "(" + v + ")"
        out[p["key"]] = v
    if probs: raise ToolError("Nothing was run. Fix these and retry:\n- " + "\n- ".join(probs))
    return out

def run_report_raw(rep, params):
    q = ";".join("%s,%s" % (k, v) for k, v in params.items() if v not in (None, ""))
    s, h, d = api("/Reports/%s/RunReport" % rep["id"], {"parameters": q} if q else None)
    if s == 204: return {}, q
    if is_oom(s, d):
        raise ToolError(OOM_PREFIX + "For report %s (id %s): pass property= (PropOwnerIDs / PropertyIDs) for fewer properties, a shorter date range (StartDate/EndDate), "
                        "or run it one property at a time and combine the results. Other built-in reports for the same data: use list_reports." % (rep["name"], rep["id"]))
    if s != 200: raise ToolError("Report failed: " + api_error(s, d))
    if isinstance(d, str):
        try: d = json.loads(d)
        except ValueError: raise ToolError("Report returned non-JSON output (%d chars)." % len(d))
    return d, q

MAX_CHUNKS = 4

def split_chunks(props, k):
    """Property dicts ({id, active}) -> k lists of IDs, round-robin over ACTIVE properties only: the report's own
    portfolio default (empty PropertyIDs) covers only active properties (verified live), so this keeps exact parity."""
    k = max(1, min(int(k), MAX_CHUNKS))
    ids = [p["id"] for p in props if p.get("active", True)]
    return [c for c in ([ids[i::k] for i in range(k)]) if c]

def merge_chunk_reports(parts, grid):
    """parts: list of (report_json, query) from chunk runs. Concatenates ONLY the named grid's rows, in chunk order
    (other grids hold per-chunk summaries that cannot be added row-wise). Returns {grid: rows}."""
    rows = []
    for d, _q in parts:
        rows += grids(d).get(grid, [])
    return {grid: rows}

def run_report_chunked(rep, params, pkey, chunks, grid):
    """Runs the report once per property chunk in parallel threads (shared throttle keeps us under the rate limit);
    a failed chunk is retried once, then the error is raised (never a silently partial answer). Returns (merged_json, query)."""
    def one(ids):
        p = dict(params); p[pkey] = "(" + ",".join(str(x) for x in ids) + ")"
        try: return run_report_raw(rep, p)
        except Exception: return run_report_raw(rep, p)   # retry once
    with ThreadPoolExecutor(len(chunks)) as ex:
        parts = list(ex.map(one, chunks))
    q = ";".join("%s,%s" % (k, v) for k, v in params.items() if v not in (None, "") and k != pkey)
    return merge_chunk_reports(parts, grid), (q + ";" if q else "") + "%s=(all, %d chunks)" % (pkey, len(chunks))

def grids(d):
    """Report JSON -> {GridName: rows}."""
    if isinstance(d, list): return {"Grid1": d}
    return {k: v for k, v in (d or {}).items() if isinstance(v, list)}

def t_run_report(id, parameters="", property="", all_properties=False, grid="", columns="", rename=None, group_by="",
                 sum="", sort="", top=0, where="", show=0, save_as="", _label=None, _opts=None, _pre=None):
    t0 = time.time()
    opts = _opts if _opts is not None else {}
    rep = report_by(id)
    params = build_report_params(rep, parameters)
    pk = _prop_param(rep)
    scope = None
    if pk:
        if property not in (None, "", "all", "*"):
            ids, scope = resolve_property_ids(property)
            params[pk] = "(" + ",".join(str(x) for x in ids) + ")"
        elif params.get(pk) and params[pk].lower() != "all":
            ids = re.findall(r"\d+", params[pk])
            scope = scope_from_filters("PropertyID,in,(%s)" % ",".join(ids))
        else:
            scope = all_scope_label()
    d, q = _pre if _pre is not None else run_report_raw(rep, params)
    g = grids(d)
    if not g: return "Report %s returned no data.\nScope: %s\nParameters: %s\n(%.1fs, live)" % (rep["name"], scope or "n/a", q or "(defaults)", time.time() - t0)
    flat_all = {k: [flatten(r) for r in v] for k, v in g.items()}
    gname = grid or max(flat_all, key=lambda k: len(flat_all[k]))
    if gname not in flat_all: raise ToolError("No grid '%s'. Grids: %s" % (gname, ", ".join(flat_all)))
    main = flat_all[gname]
    shown, tot, notes = post_process(main, columns, rename, group_by, sum, sort, top, where)
    opts["rows"] = shown   # facts source (2d): post-processed rows, before the display limit
    if opts.get("no_total"): tot = None
    jp, cp = save(save_as or _label or rep["name"], d, main)
    out = ["Report %s (id %s): %s has %d row(s)%s. Scope: %s" % (rep["name"], rep["id"], gname, len(main), "; " + "; ".join(notes) if notes else "", scope or "n/a"),
           "Parameters: %s" % (q or "(defaults)")]
    others = ["%s(%d rows)" % (k, len(v)) for k, v in flat_all.items() if k != gname]
    if others: out.append("Other grids: %s (pass grid=...)" % ", ".join(others))
    small = [k for k, v in flat_all.items() if k != gname and 0 < len(v) <= 5 and not opts.get("no_small_grids")]
    for k in small: out.append("%s:\n%s" % (k, table(flat_all[k], 5)))
    if main and not (columns or group_by): out.append("Columns: " + ", ".join(list(main[0].keys())[:60]))
    lim, large = show_plan(show, len(shown))
    body, nshown = fit_table(shown, lim, tot, MAX_TEXT - INLINE_RESERVE - len("\n".join(out)))
    out.append(body)
    out.append(deliver(shown, nshown, jp, cp, main, large))
    out.append("(%.1fs, live)" % (time.time() - t0))
    return clip("\n".join(out) + audit_note_for(rep["name"]))

# ----------------------------------------------------------------------------- PDF export
def api_bytes(path, params=None, timeout=None):
    """Binary-safe GET through rm.get_bytes with the same error mapping as api()."""
    try:
        return rm.get_bytes(path, params, timeout=timeout)
    except rm.RMError as e:
        raise ToolError(str(e))
    except OSError as e:
        if "timed out" in str(e): raise ToolError("Rent Manager did not answer within %ds; give a property and retry." % rm.DEFAULT_TIMEOUT)
        raise ToolError("Network error talking to Rent Manager: %s" % e)

def save_bytes(label, data, ext="pdf"):
    """Collision-safe exclusive create in out_dir() (same naming as save())."""
    od = out_dir()
    os.makedirs(od, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_")[:80] or "result"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 0
    while True:
        fp = os.path.join(od, "%s_%s%s.%s" % (base, stamp, "" if n == 0 else "-%d" % (n + 1), ext))
        try:
            f = open(fp, "xb"); break
        except FileExistsError:
            n += 1
    with f: f.write(data)
    return fp

def _body_note(b):
    """Short text of a non-PDF reply (JSON or error text), never huge."""
    return re.sub(r"\s+", " ", b[:300].decode("utf-8", "replace")).strip()

def fetch_report_pdf(rep, params):
    """One report -> PDF bytes. Tries getOptions=ReturnPDFStream, then ReturnPDFUrl + DownloadFile. Raises ToolError with the reason."""
    q = ";".join("%s,%s" % (k, v) for k, v in params.items() if v not in (None, ""))
    path = "/Reports/%s/RunReport" % rep["id"]
    base = {"parameters": q} if q else {}
    s, h, b = api_bytes(path, dict(base, getOptions="ReturnPDFStream"))
    if s == 200 and b[:4] == b"%PDF": return b
    if s == 204: raise ToolError("no data for these parameters (HTTP 204)")
    why = "stream HTTP %s: %s" % (s, _body_note(b))
    s2, h2, b2 = api_bytes(path, dict(base, getOptions="ReturnPDFUrl"))
    url = None
    if s2 == 200:
        try: url = json.loads(b2.decode("utf-8", "replace").lstrip("\ufeff"))
        except ValueError: url = None
    if isinstance(url, str) and url.startswith("http"):
        u = urllib.parse.urlparse(url)
        if u.netloc.lower() == urllib.parse.urlparse(rm.BASE).netloc.lower():
            s3, h3, b3 = api_bytes(u.path + ("?" + u.query if u.query else ""))
            if s3 == 200 and b3[:4] == b"%PDF": return b3
            why += "; url download HTTP %s: %s" % (s3, _body_note(b3))
        else: why += "; url host is not the Rent Manager host, not fetched"
    else:
        why += "; url HTTP %s: %s" % (s2, _body_note(b2))
    raise ToolError("this report may not support PDF (%s)" % why)

def t_report_pdf(report, properties="", parameters="", save_as=""):
    t0 = time.time()
    rep = report_by(report)
    base = build_report_params(rep, parameters)
    pk = _prop_param(rep)
    if isinstance(properties, (int, float)) and not isinstance(properties, bool): properties = [str(int(properties))]   # a bare PropertyID such as 256
    if isinstance(properties, str): properties = [x for x in re.split(r"[,;]", properties.strip("() ")) if x.strip()]
    items = [str(x).strip() for x in (properties or []) if str(x).strip() and str(x).strip().lower() not in ("all", "*")]
    if items and not pk:
        raise ToolError("%s has no property parameter; call report_pdf without properties for one PDF." % rep["name"])
    jobs = []   # (file label, display label, params)
    for it in items:
        ids, lab = resolve_property_ids(it)
        lab = re.sub(r"\s+", " ", lab.replace("Property: ", ""))
        p = dict(base); p[pk] = "(" + ",".join(str(i) for i in ids) + ")"
        jobs.append((re.sub(r"\s*\(ID \d+\)", "", lab).replace(", ", "_"), lab, p))
    if jobs:
        scope = "Property: " + "; ".join(j[1] for j in jobs)
    else:
        jobs.append(("all", "all properties", dict(base)))
        if pk and base.get(pk) and base[pk].lower() != "all":
            scope = scope_from_filters("PropertyID,in,(%s)" % ",".join(re.findall(r"\d+", base[pk])))
        else: scope = all_scope_label() if pk else "n/a"
    def one(job):
        short, label, p = job
        t1 = time.time()
        try:
            data = fetch_report_pdf(rep, p)
        except ToolError as e:
            return "%s: FAILED - %s (%.1fs)" % (label, e, time.time() - t1), False
        fp = save_bytes("%s_%s" % (save_as or rep["name"], short), data)
        return "%s: saved (%.0f KB, %.1fs)\n%s" % (label, len(data) / 1024.0, time.time() - t1, file_lines(fp, hint=False)), True
    with ThreadPoolExecutor(min(4, len(jobs))) as ex:
        res = list(ex.map(one, jobs))
    head = "Report %s (id %s) as PDF: %d of %d saved." % (rep["name"], rep["id"], sum(1 for r in res if r[1]), len(res))
    tail = "Scope: %s\nParameters: %s\n(%.1fs, live)" % (scope, ";".join("%s,%s" % (k, v) for k, v in base.items() if v not in (None, "")) or "(defaults)", time.time() - t0)
    if any(r[1] for r in res): tail += "\n" + FILE_HINT
    if not any(r[1] for r in res): tail += "\nNo PDF was produced; use run_report for this report's data instead."
    return head + "\n" + "\n".join(r[0] for r in res) + "\n" + tail

# ----------------------------------------------------------------------------- recipes
def load_recipes():
    out = []
    if not os.path.isdir(RECIPES): return out
    for fn in sorted(os.listdir(RECIPES)):
        if not fn.endswith(".json"): continue
        try:
            with open(os.path.join(RECIPES, fn), encoding="utf-8") as f: r = json.load(f)
            r["_file"] = fn; out.append(r)
        except ValueError as e:
            sys.stderr.write("bad recipe %s: %s\n" % (fn, e))
    return out

def recipe_search(query):
    qt = SI.query_terms(query)
    res = []
    for r in load_recipes():
        terms = set(SI.tokens(r["name"]) + SI.tokens(r.get("description", "")))
        kw = set(t for k in r.get("keywords", []) for t in SI.tokens(k)) | {k.lower().replace(" ", "") for k in r.get("keywords", [])}
        sc = sum(w * (3 if t in kw else 1.5 if t in terms else 0) for t, w in qt.items())
        if sc > 0: res.append((round(sc, 1), r))
    res.sort(key=lambda x: -x[0])
    return res

def has_period(spec):
    low = {k.lower() for k in spec}
    return "start" in low and "end" in low

def period_range(p):
    """this_month | last_month | ytd | t12 | YYYY-MM | 'September 2026' | 'Sep' -> (start, end) ISO dates."""
    sp = str(p).strip()
    if re.fullmatch(r"\d{4}-\d\d-\d\d", sp): raise ToolError("period takes a month, quarter or year (this_month, last_month, ytd, t12, 2026-09, 'September 2026', 'Q3 2026', 2025); for exact dates pass start and end, e.g. start='%s', end='%s'." % (sp, sp))
    return parse_date(sp), parse_date(sp, end=True)

def unknown_recipe_msg(name, recs):
    """1b: near-miss recipe name -> name the exact call (never auto-run)."""
    cand = difflib.get_close_matches(str(name).lower(), [k.lower() for k in recs], n=1, cutoff=0.6)
    guess = next((k for k in recs if k.lower() == cand[0]), None) if cand else None
    if not guess:
        hit = recipe_search(str(name).replace("_", " "))
        guess = hit[0][1]["name"] if hit else None
    if guess:
        return "Unknown recipe '%s'. Did you mean run_recipe(name='%s')? (not run automatically). Available: %s" % (name, guess, ", ".join(recs))
    return "Unknown recipe '%s'. Available: %s. e.g. run_recipe(name='%s')" % (name, ", ".join(recs), next(iter(recs), "collections"))

def _recipe_key(n):
    """'Rent Roll' | 'rent-roll' | ' Collections ' -> 'rent_roll' | 'collections'."""
    return re.sub(r"[\s-]+", "_", str(n).strip().lower())

REPORT_PARAMS_SPEC = {"type": "str", "default": "", "description": "Any other option of the built-in report as 'Key,Value;Key2,Value2' (keys from report_info); the report's own defaults apply"}

def recipe_spec(r):
    """The recipe's params plus, for report recipes, the implicit pass-through `report_params` (every UI option of the report)."""
    spec = dict(r.get("params") or {})
    if "report" in (r.get("request") or {}): spec.setdefault("report_params", REPORT_PARAMS_SPEC)
    return spec

def flatten_recipe_args(args):
    """1e: top-level keys that are params of the named recipe move into params."""
    args = dict(args)
    recs = {r["name"].lower(): r for r in load_recipes()}
    r = recs.get(_recipe_key(args.get("name", "")))
    if not r: return args
    args["name"] = r["name"]
    spec = {k.lower() for k in recipe_spec(r)} | {a.lower() for ps in recipe_spec(r).values() for a in ps.get("aliases", [])}
    if has_period(r.get("params") or {}): spec.add("period")
    params = args.get("params")
    if isinstance(params, str):
        try: params = json.loads(params) if params.strip() else {}
        except ValueError: return args  # let run_recipe report it
    params = dict(params or {})
    inner = params.get("params")   # nested params.params
    if isinstance(inner, str):
        try: inner = json.loads(inner)
        except ValueError: inner = None
    if isinstance(inner, dict):
        params.pop("params"); params = dict(inner, **params)
    for k in [k for k in args if k not in ("name", "params", "show", "all_properties") and k.lower() in spec]:
        params.setdefault(k, args.pop(k))
    if params: args["params"] = params
    return args

# ----------------------------------------------------------------------------- recipe catalog (1a)
DESC_LIMIT = 1900

def _cut(d, n):
    if len(d) <= n: return d
    return d[:n].rsplit(" ", 1)[0].rstrip(",:;-") if " " in d[:n] else d[:n]

def recipe_catalog(limit=1500):
    """One line per recipe: name(params): purpose. Total length <= limit (purposes are shortened to fit)."""
    lines = []
    for r in load_recipes():
        if r.get("catalog") is False: continue   # 4b: rare recipes stay runnable/findable but are not advertised
        spec = r.get("params") or {}
        names = [k for k, v in spec.items() if k.lower() not in ("start", "end") and not v.get("advanced")]   # advanced knobs are documented by find_recipe / errors, not the catalog
        if has_period(spec): names.insert(1 if names[:1] == ["property"] else 0, "period")
        shown = names[:3]
        first = re.split(r"(?<=[a-z\)])\.\s|;\s|\s\(", r.get("description", "").strip().rstrip("."))[0]
        lines.append(["%s(%s)" % (r["name"], ",".join(shown)), first, r.get("use", "")])
    def render(n):   # 4a: `use` is printed untruncated; only the fallback description is shortened
        return "\n".join("%s: %s" % (h, u) if u else "%s: %s" % (h, d[:n].rstrip()) if n and d else h for h, d, u in lines)
    n = 120
    while n > 0 and len(render(n)) > limit: n -= 4
    return render(max(n, 0))

def _param_line(name, p):
    return "%s (%s%s)%s" % (name, p.get("type", "str"), (", default %s" % p["default"] if p["default"] != "" else ", optional") if "default" in p else ", REQUIRED",
                            ": " + p["description"] if p.get("description") else "")

PDF_WORD = re.compile(r"\bpdfs?\b", re.I)

def t_find_recipe(query, limit=3):
    if PDF_WORD.search(query):   # PDF export is a tool, not a recipe: point at report_pdf
        q = PDF_WORD.sub(" ", query)
        rid = "67" if re.search(r"rent\s*roll", q, re.I) else next((h for _, h in SI.search(q, "reports", 1)), "67")
        return ("PDF requested: use report_pdf(report=%s, properties='name1, name2'): one PDF file per property in out/ "
                "(add parameters='Key,Value;...' for report options; report_info(%s) lists them)." % (rid, rid))
    hits = recipe_search(query)[: int(limit)]
    cl = concept_lines(query) + suggestion_lines(query)
    if not hits:
        names = ", ".join(r["name"] for r in load_recipes())
        return "\n".join(cl + ["No recipe matches '%s'. Available: %s\nOtherwise use search_models / list_reports." % (query, names)])
    out = []
    for sc, r in hits:
        out.append("%s: %s\n  params: %s" % (r["name"], r["description"], "; ".join(_param_line(k, v) for k, v in recipe_spec(r).items()) or "none"))
    out.append("Next: run_recipe(name='%s', params={...})" % hits[0][1]["name"])
    return "\n".join(cl + out)

def _fill(template, values):
    """Replace {name} placeholders. Returns None if the template is only a placeholder whose value is empty."""
    if not isinstance(template, str): return template
    if template.startswith("?") and "|" in template:   # '?flag|clause' keeps clause only when bool param flag is true ('?!flag|' = when false)
        cond, _, template = template[1:].partition("|")
        neg = cond.startswith("!")
        if (str(values.get(cond.lstrip("!"))).lower() in ("true", "1", "yes", "y")) == neg: return None
    m = re.fullmatch(r"\{(\w+)\}", template)
    if m and values.get(m.group(1)) in (None, ""): return None
    def rep(mm):
        v = values.get(mm.group(1), "")
        return "" if v is None else str(v)
    res = re.sub(r"\{(\w+)\}", rep, template)
    if re.search(r"\(\)$|,\s*$|,\)$", res) and any(values.get(k) in (None, "") for k in re.findall(r"\{(\w+)\}", template)):
        return None  # e.g. 'PropertyID,in,({property})' with no property -> drop clause
    return res

# ----------------------------------------------------------------------------- headline + facts (v2 item 2)
# Template tokens (all computed from the recipe's post-processed rows; money tokens end in $):
#   {count}  rows | {sum:Col} / {sum$:Col}  column total | {top1:Col}  value in the first row | {last:Col} value in the last row
#   {sumif:Col|MatchCol|text}  total of Col over rows whose MatchCol contains text | {pct:a/b}  sum(a)/sum(b) as a percent | {name} / {name$}  a fact (noi, income, ...) or a recipe param (as_of, start, end)
_TOKEN = re.compile(r"\{(\w+)(\$?)(?::([^{}]+))?\}")

def _money(x):
    return ("-" if x < 0 else "") + "$" + "{:,.2f}".format(abs(x))

def _plain(x):
    return "{:,}".format(int(x)) if float(x).is_integer() else "{:,.2f}".format(x)

def _col(rows, col):
    if rows and col not in rows[0]:
        raise ToolError("unknown column '%s'. Did you mean: %s" % (col, ", ".join(suggest(col, rows[0].keys()))))

def _colsum(rows, col):
    _col(rows, col)
    v = round(sum(_num(r.get(col)) or 0.0 for r in rows), 2)
    return int(v) if float(v).is_integer() else v

def eval_token(kind, arg, rows, vals, facts):
    """Raw value of one token (number, str or None)."""
    if kind == "count": return len(rows)
    if kind == "sum": return _colsum(rows, arg)
    if kind == "sumif":   # {sumif:Col|MatchCol|text} total of Col over rows whose MatchCol contains text (case-insensitive)
        col, mcol, text = ([x.strip() for x in (arg or "").split("|")] + ["", "", ""])[:3]
        _col(rows, col); _col(rows, mcol)
        v = round(sum(_num(r.get(col)) or 0.0 for r in rows if text.lower() in str(r.get(mcol) or "").lower()), 2)
        return int(v) if float(v).is_integer() else v
    if kind == "newest":   # value in the chronologically newest row (rows may be oldest-first or newest-first)
        if not rows: return None
        _col(rows, arg)
        dk = next((k for k in ("TransactionDate", "MoveOutDate", "FromDate") if k in rows[0]), None)
        row = rows[0] if dk and str(rows[0].get(dk) or "") >= str(rows[-1].get(dk) or "") else rows[-1]
        v = row.get(arg)
        return _num(v) if _num(v) is not None and not isinstance(v, str) else v
    if kind in ("top1", "last"):
        if not rows: return None
        _col(rows, arg)
        v = (rows[0] if kind == "top1" else rows[-1]).get(arg)
        return _num(v) if _num(v) is not None and not isinstance(v, str) else v
    if kind == "pct":
        a, _, b = (arg or "").partition("/")
        den = _colsum(rows, b.strip())
        return None if not den else round(100.0 * _colsum(rows, a.strip()) / den, 1)
    if kind in facts: return facts[kind]
    if kind in vals: return vals[kind]
    raise ToolError("unknown headline token '%s'" % kind)

def _render(kind, money, raw):
    if raw is None or raw == "": return "n/a"
    if kind == "pct": return "%.1f%%" % raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool): return _money(raw) if money else _plain(raw)
    return _fmt(raw, 80)

def render_headline(template, rows, vals, facts):
    return _TOKEN.sub(lambda m: _render(m.group(1), m.group(2) == "$", eval_token(m.group(1), m.group(3), rows, vals, facts)), template)

def compute_facts(recipe, rows, vals, scope=None):
    """One source of truth for numbers: the headline and (later) eval anchors both read this dict."""
    facts = {"count": len(rows)}
    for c in [x.strip() for x in ((recipe.get("post") or {}).get("sum") or "").split(",") if x.strip()]:
        try: facts["sum." + c] = _colsum(rows, c)
        except ToolError: pass
    noi = (recipe.get("post") or {}).get("noi")
    if noi:   # P&L: NOI = income - expense, computed here so the model never subtracts (the grid's TOTAL row mixes both)
        def part(label):
            return round(sum(_num(r.get(noi["value"])) or 0.0 for r in rows if str(r.get(noi["label"]) or "").lower() == label.lower()), 2)
        facts["income"], facts["expense"] = part(noi["income"]), part(noi["expense"])
        facts["noi"] = round(facts["income"] - facts["expense"], 2)
    for name, tok in (recipe.get("facts") or {}).items():   # named aliases, e.g. total_owed -> sum:Owed
        kind, _, arg = tok.partition(":")
        facts[name] = eval_token(kind, arg, rows, vals, facts)
    for k in ("as_of", "start", "end"):
        if vals.get(k): facts[k] = vals[k]
    if scope: facts["scope"] = scope
    return facts

def tenant_terms(v):
    """tenant param -> list of names/ids: a list, a JSON list, or ONE name; several names in a string are separated by ';' (a ',' is part of 'Last, First')."""
    if isinstance(v, (list, tuple)): items = list(v)
    else:
        t = str(v).strip()
        if t.startswith("["):
            try: items = json.loads(t)
            except ValueError: items = [t]
        else: items = t.split(";")
    items = [str(x).strip() for x in items if str(x).strip()]
    if not items: raise ToolError("tenant is empty: give a tenant name or TenantID (several: a list).")
    return items

def resolve_tenant(term, property_ids=None):
    term = str(term).strip()
    if term.isdigit(): return int(term), "Tenant ID %s" % term
    flt = "Name,ct,%s" % term.replace(",", " ")
    if property_ids: flt += ";PropertyID,in,(%s)" % ",".join(map(str, property_ids))
    s, h, d = api("/Tenants", {"filters": flt, "fields": "TenantID,Name,PropertyID", "pageSize": 10})
    if s == 204 or not d: raise ToolError("No tenant matches '%s'. Try last name only, or resolve(term, kind='tenant')." % term)
    if s != 200: raise ToolError(api_error(s, d))
    exact = [t for t in d if _norm(t["Name"]) == _norm(term)]
    if len(d) == 1 or len(exact) == 1:
        t = (exact or d)[0]; return t["TenantID"], "Tenant: %s (ID %s)" % (t["Name"], t["TenantID"])
    raise ToolError("'%s' matches several tenants: %s. Ask the user which, then pass the TenantID." %
                    (term, "; ".join("%s=ID %s (property %s)" % (t["Name"], t["TenantID"], t["PropertyID"]) for t in d[:8])))

AUDIT_NOTE = ("Lease audit note: if this is the basis of a lease audit, include rental assistance (RA) charges as rent; rent roll rows can be split "
              "(tenant row + subsidy row for the same unit), so sum the rows per unit; rent roll has no lease dates or IDs: join to Leases for those.")
_AUDIT_NAME = re.compile(r"rent\s*_?roll|recurring\s*_?charge", re.I)

def audit_note_for(*names):
    return ("\n" + AUDIT_NOTE) if any(_AUDIT_NAME.search(str(n or "")) for n in names) else ""

def t_run_recipe(name, params=None, all_properties=False, show=0):
    text = run_recipe_result(name, params, all_properties, show)["text"]
    rec = next((r for r in load_recipes() if _recipe_key(r["name"]) == _recipe_key(str(name))), None) or {}
    return text + audit_note_for(rec.get("name"), rec.get("report"))

def parse_basis(v):
    """basis param -> (eBasis value or "", label). Cash=2, Accrual=1; unspecified keeps the Rent Manager default (Cash)."""
    t = str(v or "").strip().lower()
    if t in ("", "default", "none"): return "", "Cash (Rent Manager default)"
    if t in ("cash", "2"): return 2, "Cash"
    if t in ("accrual", "1"): return 1, "Accrual"
    raise ToolError("basis must be Cash or Accrual, e.g. params={'basis': 'Accrual'}")

def _param_num(v, pname, name):
    try: return float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError: raise ToolError("Param '%s' must be a number, got '%s'. e.g. run_recipe(name='%s', params={'%s': 100})" % (pname, v, name, pname))

def run_recipe_result(name, params=None, all_properties=False, show=0):
    """Runs a recipe. Returns {"text", "headline", "facts", "scope"}; text (the tool output) is rendered from the same facts (2d)."""
    t0 = time.time()
    if isinstance(params, str):
        try: params = json.loads(params) if params.strip() else {}
        except ValueError: raise ToolError("params must be a JSON object, e.g. {\"property\": \"Oak Court\", \"period\": \"last month\"}")
    params = dict(params or {})
    recs = {r["name"]: r for r in load_recipes()}
    r = recs.get(name) or next((x for k, x in recs.items() if k.lower() == _recipe_key(name)), None)
    if not r: raise ToolError(unknown_recipe_msg(name, recs))
    spec = recipe_spec(r)
    if has_period(spec) and params.get("period") not in (None, ""):  # one 'period' sets start+end
        ps, pe = period_range(params.pop("period"))
        params.setdefault("start", ps); params.setdefault("end", pe)
    params.pop("period", None)
    lowp = {k.lower(): v for k, v in params.items()}
    amap = {a.lower(): pn for pn, ps in spec.items() for a in ps.get("aliases", [])}   # old param names -> current name (5e)
    for a, pn in amap.items():
        if a in lowp and pn.lower() not in lowp: lowp[pn.lower()] = lowp[a]
    bad = [k for k in params if k.lower() not in {s.lower() for s in spec} and k.lower() not in amap]
    if bad: raise ToolError("Unknown param(s) %s for %s. Params: %s. e.g. run_recipe(name='%s', params={%s})" % (bad, name, "; ".join(_param_line(k, v) for k, v in spec.items()), name, ", ".join("'%s': ..." % k for k in list(spec)[:2])))
    vals, scope, prop_ids, warn, basis_label = {}, None, [], "", ""
    for pname, p in sorted(spec.items(), key=lambda kv: kv[1].get("type") != "property"):  # property first (narrows tenant search)
        v = lowp.get(pname.lower(), p.get("default"))
        typ = p.get("type", "str")
        if v is None and "default" not in p:
            raise ToolError("Missing required param '%s': %s. e.g. run_recipe(name='%s', params={'%s': ...})" % (pname, p.get("description", ""), name, pname))
        if typ == "date": v = parse_date(v, end=pname.lower() in ("end", "end_date", "to", "until")) if v not in (None, "") else ""
        elif typ == "int": v = "" if v in (None, "") else int(_param_num(v, pname, name)) + int(p.get("offset", 0))
        elif typ == "number": v = "" if v in (None, "") else round(_param_num(v, pname, name) + float(p.get("offset", 0)), 2)
        elif typ == "basis": v, basis_label = parse_basis(v)
        elif typ == "bool": v = "True" if str(v).lower() in ("1", "true", "yes", "y") else "False"
        elif typ == "property":
            if isinstance(v, str) and v.lower() in ("all", "*"): v = ""
            prop_ids, pl = resolve_property_ids(v)
            v = ",".join(str(x) for x in prop_ids)
            if pl: scope = pl + ("; " + scope if scope else "")
        elif typ == "tenant":
            ids_, tls = [], []
            for term in tenant_terms(v):
                i_, tl_ = resolve_tenant(term, prop_ids)
                if i_ not in ids_: ids_.append(i_); tls.append(tl_)
            if len(ids_) > 1 and not r.get("multi_tenant"): raise ToolError("%s takes one tenant per call." % name)
            v = ids_[0]
            vals["_tenants"] = [(i_, re.sub(r"^Tenant:? ?", "", t_)) for i_, t_ in zip(ids_, tls)]
            scope = (scope + "; " if scope else "") + "; ".join(tls)
        vals[pname] = v
    vals["as_of_is_today"] = "True" if vals.get("as_of") in (None, "", _today().isoformat()) else "False"
    if not scope:
        if r.get("heavy") and any(p.get("type") == "property" for p in spec.values()):
            warn = scope_warning("Recipe %s" % name)
        scope = all_scope_label()
    req = r["request"]
    vals["_scope"] = scope
    if "bundle" in req:
        return run_bundle(r, vals, show, t0)
    opts = {"no_small_grids": bool(r.get("no_small_grids")), "no_total": bool(r.get("no_total"))}
    post = {k: _fill(v, vals) if isinstance(v, str) else v for k, v in (r.get("post") or {}).items()}
    common = dict(columns=post.get("columns", ""), group_by=post.get("group_by", ""), sum=post.get("sum", ""),
                  sort=post.get("sort", ""), top=post.get("top", 0), where=post.get("where", ""), show=show)
    if "report" in req:
        rp = {k: _fill(v, vals) for k, v in (req.get("parameters") or {}).items()}
        rp = {k: v for k, v in rp.items() if v is not None}
        if vals.get("report_params") not in (None, ""):   # any other report option, validated against the report's parameter list
            rp.update(build_report_params(report_by(recipe_report(req)), vals["report_params"]))
        pre = None
        pkeys = [k for k, v in (req.get("parameters") or {}).items() if isinstance(v, str) and "{property}" in v]
        if int(r.get("chunks") or 1) > 1 and not prop_ids and req.get("grid") and len(pkeys) == 1 and pkeys[0] not in rp:
            try: props = properties(live=True)   # v4 1a: never the alias file; on failure run unchunked
            except ToolError: props = []
            chunks = split_chunks(props, r["chunks"])
            if len(chunks) > 1:   # portfolio-wide run: split the property list (2b)
                rep = report_by(recipe_report(req))
                pre = run_report_chunked(rep, build_report_params(rep, rp), _prop_param(rep) or pkeys[0], chunks, req["grid"])
        res = t_run_report(recipe_report(req), rp, property="", all_properties=True, grid=req.get("grid", ""),
                           rename=post.get("rename"), save_as=name, _opts=opts, _pre=pre, **common)
        sc_line = "Scope: " + scope.replace("\\", "/") + ("\nBasis: " + basis_label if basis_label else "")
        res = re.sub(r"Scope: [^\n]*", lambda m: sc_line, res, count=1)
    else:
        qd = req["query"]
        flt = [_fill(c, vals) for c in qd.get("filters", [])]
        flt = ";".join(c for c in flt if c)
        if r.get("multi_tenant"):
            res = _ledger_recipe(r, vals, flt, post, name, scope, common, opts)
        else:
            res = _query_renamed(qd, vals, flt, post, name, scope, common, opts)
    facts, headline = {}, ""
    if r.get("reconcile") and opts.get("rows") is not None:
        try: res += reconcile_recipes(r["reconcile"], vals, opts["rows"])
        except ToolError as e: res += "\n(reconciliation not available: %s)" % e
    if "rows" in opts:
        facts = opts.get("facts") or compute_facts(r, opts["rows"], vals, scope)
        try:
            if opts.get("headline"): headline = opts["headline"]
            elif r.get("headline"): headline = render_headline(r["headline"], opts["rows"], vals, facts)
        except ToolError as e:
            res += "\n(headline not available: %s)" % e
    text = "Recipe %s. %s%s\n%s\n(total %.1fs)" % (name, r.get("answer_hint", ""), ("\n" + warn) if warn else "", res, time.time() - t0)
    if headline: text = "Answer: %s\n%s" % (headline, text)
    if opts.get("cap"): text = "\n".join(opts["cap"]) + "\n" + text   # CAPPED / PARTIAL lines come FIRST
    text = lift_notice(text)   # then the MORE notice (rows missing), above Answer/Recipe/the table
    return {"text": text, "headline": headline, "facts": facts, "scope": scope, "rows": opts.get("rows")}

def reconcile_recipes(rec, vals, rows):
    """Compare this recipe's rows (by UnitID) with another recipe's rows (default vacant_units, the physical definition) and say why
    units differ. Never hides a difference: lists them with a reason."""
    other = run_recipe_result(rec["recipe"], {"property": vals.get("property", ""), "as_of": vals.get("as_of", "")}, show=1)
    orows = other.get("rows") or []
    mine = {str(x.get("UnitID")): x for x in rows if x.get("UnitID") not in (None, "")}
    theirs = {str(x.get("UnitID")): x for x in orows}
    only_other = [theirs[k] for k in theirs if k not in mine]
    only_mine = [mine[k] for k in mine if k not in theirs]
    def fmt(lst, why):
        items = ["%s %s (%s)" % (x.get("property") or "", x.get("unit") or "?", why(x)) for x in lst[:15]]
        return "; ".join(items) + (" ... +%d more" % (len(lst) - 15) if len(lst) > 15 else "")
    out = ["Reconciliation (%s): %d units here vs %d in %s. Definition used by %s: %s" % (
        rec["recipe"], len(mine), len(theirs), rec["recipe"], rec["recipe"], rec.get("definition", "see that recipe"))]
    if not only_other and not only_mine:
        out.append("Same units in both.")
    if only_other:
        out.append("In %s but NOT here (%d): %s" % (rec["recipe"], len(only_other), fmt(only_other, lambda x: rec.get("why_other", "omitted by this report") + (", unit type %s" % x.get("unit_type") if x.get("unit_type") else ""))))
    if only_mine:
        out.append("Here but NOT in %s (%d): %s" % (rec["recipe"], len(only_mine), fmt(only_mine, lambda x: rec.get("why_mine", "excluded by that recipe's scope (inactive property, other rentable item)"))))
    return "\n" + "\n".join(out)

def run_bundle(r, vals, show, t0):
    """Bundle recipe: request.bundle = [{"recipe": name, "params": {...templated...}, "label": "..."}].
    Parts run in parallel (the shared throttle in rm.py keeps us under 500 req/min); one combined answer:
    Scope line, each part's headline, FAILED parts, then the full part outputs."""
    parts = r["request"]["bundle"]
    def one(part):
        p = {k: _fill(v, vals) for k, v in (part.get("params") or {}).items()}
        p = {k: v for k, v in p.items() if v not in (None, "")}
        t1 = time.time()
        label = part.get("label") or part["recipe"]
        try:
            res = run_recipe_result(part["recipe"], p, show=part.get("show", 15))
            res["ok"] = True
        except Exception as e:   # one failed part must not hide the others
            res = {"ok": False, "text": "FAILED: %s" % e, "headline": "", "facts": {}, "error": str(e)}
        res["label"], res["secs"] = label, time.time() - t1
        return res
    deadline = float(os.environ.get("RM_DEADLINE", "50"))
    ex = ThreadPoolExecutor(max(1, min(8, len(parts))))
    futs = [ex.submit(one, part) for part in parts]
    results = []
    for part, f in zip(parts, futs):
        label = part.get("label") or part["recipe"]
        try:
            results.append(f.result(timeout=max(0.1, deadline - (time.time() - t0))))
        except FuturesTimeout:   # soft deadline (3i): return finished parts, name the unfinished one
            results.append({"ok": False, "text": "", "headline": "", "facts": {}, "label": label, "secs": time.time() - t0,
                            "error": "not finished within %.0fs; rerun this part alone or with a property filter" % deadline})
    ex.shutdown(wait=False, cancel_futures=True)
    failed = [x for x in results if not x["ok"]]
    out = ["Bundle %s: %d parts run in parallel (%d ok, %d FAILED). %s" % (r["name"], len(parts), len(results) - len(failed), len(failed), r.get("answer_hint", "")),
           "Scope: %s" % (vals.get("_scope") or all_scope_label()).replace("\\", "/")]
    for x in results:
        if not x["ok"]: out.append("FAILED part [%s]: %s" % (x["label"], x["error"]))
        elif x["headline"]: out.append("Answer [%s]: %s" % (x["label"], x["headline"]))
    for x in results:
        body = re.sub(r"^Answer: [^\n]*\n", "", x["text"], count=1, flags=re.M)
        out.append("\n=== %s (%.1fs) ===\n%s" % (x["label"], x["secs"], body))
    out.append("(bundle total %.1fs)" % (time.time() - t0))
    text = lift_notice("\n".join(out))
    if len(text) > MAX_TEXT * 3: text = text[: MAX_TEXT * 3] + "\n...[clipped; each part's full data is in its saved file]"
    return {"text": text, "headline": "", "facts": {"parts": {x["label"]: x["facts"] for x in results}, "failed": [x["label"] for x in failed]},
            "scope": vals.get("_scope")}

def apply_calc(row, calc):
    """calc = {"newcol": "col" | "a-b" | "pct:a/b"} evaluated on one row (pct = percent, 1 decimal, None if b is 0)."""
    for name, expr in calc.items():
        e = expr.strip()
        if e.startswith("pct:"):
            a, _, b = e[4:].partition("/")
            den = _num(row.get(b.strip()))
            row[name] = None if not den else round(100.0 * (_num(row.get(a.strip())) or 0.0) / den, 1)
        elif "-" in e:
            a, _, b = e.partition("-")
            v = round((_num(row.get(a.strip())) or 0.0) - (_num(row.get(b.strip())) or 0.0), 2)
            row[name] = int(v) if float(v).is_integer() else v
        else:
            v = row.get(e)
            row[name] = int(v) if isinstance(v, float) and v.is_integer() else v

def _also_rows(r, qd, al, vals, have):
    """post.also: a second fetch that ADDS rows the main filter cannot express (e.g. units reserved by a prospect or on a status hold
    that the strict IsVacantAsOf filter drops). keep = embed/column that must be truthy (IsVacant). Rows already present are skipped."""
    aflt = ";".join(c for c in (_fill(x, vals) for x in al["filters"]) if c)
    keyf, keep = al.get("key", "UnitID"), al.get("keep")
    flds = ",".join(x for x in [qd.get("fields", ""), keep] if x)
    emb = ",".join(x for x in [qd.get("embeds", ""), keep] if x)
    fl, em, flt, _s, probs = validate(r["model"], flds, emb, aflt, "")
    if probs: raise ToolError("Recipe also query invalid (fix recipe file): " + "; ".join(probs))
    rows, _t, _p, _st = fetch_ordered(r["path"], {"fields": fl, "embeds": em, "filters": flt, "orderingOptions": ""}, _rec_limit(qd))
    if isinstance(rows, dict): rows = [rows]
    seen = {str(x.get(keyf)) for x in have}
    out = []
    for x in rows or []:
        f = flatten(x)
        if keep and not f.get(keep): continue
        if str(f.get(keyf)) in seen: continue
        for k in fl.split(","): f.setdefault(k, None)
        out.append(f)
    return out

def _rec_limit(qd, vals=None):
    """Row limit of a recipe query: its max_records (a number or a '{param}' template), clamped to RM_MAX_ROWS_HARD."""
    mr = qd.get("max_records", MAX_ROWS_HARD)
    if isinstance(mr, str): mr = _fill(mr, vals or {})
    try: return row_limit(int(mr))
    except (TypeError, ValueError): return MAX_ROWS_HARD

def _query_renamed(qd, vals, flt, post, name, scope, common, opts=None, ext=None):
    """Query + rename columns (recipes use this to give friendly column names like status_flag).
    ext (a dict): collect the processed rows and cap information in it and return "" (no file, no text): used by multi-tenant recipes."""
    t0 = time.time()
    r = find_resource(_fill(qd["resource"], vals))
    fl, em, flt2, (lsort, order), probs = validate(r["model"], qd.get("fields", ""), qd.get("embeds", ""), flt, qd.get("orderby", ""))
    if probs: raise ToolError("Recipe query invalid (fix recipe file):\n- " + "\n- ".join(probs))
    prm = {"fields": fl, "embeds": em, "filters": flt2, "orderingOptions": order}
    limit = _rec_limit(qd, vals)
    nc = post.get("newest_first") if (post.get("newest_first") and order == post.get("newest_first")) else ""   # keep the NEWEST rows under the cap
    rows, total, pages, s = fetch_ordered(r["path"], prm, limit, newest_col=nc, partial_ok=True)
    if order and not prm.get("orderingOptions") and not common["sort"]: lsort = order
    rows_obj = rows
    if isinstance(rows, dict): rows = [rows]
    capline = ""
    if getattr(rows_obj, "partial", ""):
        capline = "PARTIAL: only the first %d of %s rows were read before Rent Manager stopped. %s" % (len(rows), total if total >= len(rows) else "an unknown number of", rows_obj.partial)
    elif isinstance(rows_obj, list) and total > len(rows):
        kind = cap_kind(rows_obj, order)
        capline = cap_line(len(rows), total, kind, r["path"], limit)
    if capline and opts is not None: opts.setdefault("cap", []).append(capline)
    flat = [flatten(x) for x in rows]
    for f in flat:
        for k in [x for x in fl.split(",") if x] + list(post.get("rename") or {}):
            f.setdefault(k, None)
    al = post.get("also")
    def_notes = []
    if al and str(vals.get("as_of_is_today")) == "True":   # reserved/held units are physically vacant; only knowable for today
        flat += _also_rows(r, qd, al, vals, flat)
    elif al:   # past date: only the strict filter exists. Say so, with how many units today's definition would add (no silent switch)
        try:
            extra = len(_also_rows(r, qd, al, vals, flat))
            diff = "today's definition would add %d unit(s) that are reserved/held and physically vacant NOW" % extra
        except ToolError:
            diff = "the difference to today's definition could not be computed"
        def_notes.append("STRICT definition for past as_of %s: units reserved by a prospect or on a unit-status hold are NOT counted (their past state is unknowable); %s, so this count is NOT comparable to a count for today" % (vals.get("as_of"), diff))
    for col, mp in (post.get("value_map") or {}).items():   # friendly labels for coded columns (before rename)
        for f in flat:
            if col in f and f[col] in mp: f[col] = mp[f[col]]
    fc = post.get("family_col")   # label rent-family charge rows: {"col": "rent_type", "from": "Description"}
    if fc:
        for f in flat:
            f[fc["col"]] = rent_type_label(f.get(fc["from"]), f.get("TransactionType", "Charge"))
    lk = post.get("lookup")
    if lk and flat:   # live join against a small reference table (one extra GET), e.g. LeaseTermID -> term name + month-to-month flag
        ls_, _, ld = api("/" + lk["resource"], {"pageSize": 1000})
        ref = {str(x.get(lk["key"])): x for x in ld} if ls_ == 200 and isinstance(ld, list) else {}
        for f in flat:
            hit = ref.get(str(f.get(lk["col"])))
            for src, dst in lk["values"].items():
                f[dst] = (hit.get(src) if src in hit else False) if hit is not None and src.startswith("Is") else (hit.get(src) if hit is not None else None)
    mk = post.get("mark")
    if mk and flat:   # second fetch: flag rows whose key is in a filtered set (e.g. vacant UnitIDs), column = 1/0
        mflt = ";".join(c for c in (_fill(x, vals) for x in mk.get("filters", [])) if c)
        _f, _e, mflt2, (_s, _o), mprobs = validate(r["model"], mk["key"], "", mflt, "")
        if mprobs: raise ToolError("Recipe mark query invalid (fix recipe file):\n- " + "\n- ".join(mprobs))
        mrows, _t, _p, _s2 = fetch_ordered(r["path"], {"fields": _f, "embeds": "", "filters": mflt2, "orderingOptions": ""}, _rec_limit(qd))
        if isinstance(mrows, dict): mrows = [mrows]
        hit = {str(x.get(mk["key"])) for x in (mrows or [])}
        if mk.get("also") and str(vals.get("as_of_is_today")) == "True":
            hit |= {str(x.get(mk["key"])) for x in _also_rows(r, {"fields": mk["key"], "max_records": _rec_limit(qd)}, mk["also"], vals, [])}
        elif mk.get("also"):
            try:
                extra = len(_also_rows(r, {"fields": mk["key"], "max_records": _rec_limit(qd)}, mk["also"], vals, []))
                diff = "today's definition would add %d reserved/held unit(s) that are physically vacant NOW" % extra
            except ToolError:
                diff = "the difference to today's definition could not be computed"
            def_notes.append("STRICT vacancy definition for past as_of %s: units reserved by a prospect or on a unit-status hold are NOT counted; %s, so this is NOT comparable to a count for today" % (vals.get("as_of"), diff))
        for f in flat: f[mk["col"]] = 1 if str(f.get(mk["key"])) in hit else 0
    rename = post.get("rename") or {}
    if post.get("calc"):   # group first, then derived columns (count, a-b, pct:a/b), then sort/columns/top/total
        shown, tot, notes = post_process(flat, "", rename, common["group_by"], post.get("group_sum", ""), "", 0, common["where"], post.get("running"))
        for row in shown: apply_calc(row, post["calc"])
        shown, tot, n2 = post_process(shown, common["columns"], None, "", common["sum"], common["sort"] or lsort, common["top"], "", None)
        notes += n2
    else:
        shown, tot, notes = post_process(flat, common["columns"], rename, common["group_by"], common["sum"],
                                         common["sort"] or lsort, common["top"], common["where"], post.get("running"),
                                         newest_first=post.get("newest_first") or None)
    notes = notes + def_notes
    renamed = [{rename.get(k, k): v for k, v in f.items()} for f in flat]
    if opts is not None:
        opts["rows"] = shown
        if opts.get("no_total"): tot = None
    if ext is not None:
        ext.update(shown=shown, flat=flat, raw=list(rows), total=total, nrec=len(flat), path=r["path"], capline=capline, notes=notes,
                   newest=bool(getattr(rows_obj, "newest", False)), partial=bool(getattr(rows_obj, "partial", "")), pages=pages)
        return ""
    jp, cp = save(name, rows, renamed)
    head = "%s: %d of %d record(s)%s. Scope: %s" % (r["path"], len(flat), total, "; " + "; ".join(notes) if notes else "", scope)
    lim, large = show_plan(common["show"], len(shown))
    body, nshown = fit_table(shown, lim, tot, MAX_TEXT - INLINE_RESERVE - len(head))
    return clip("%s\n%s\n%s\n(%.1fs, %d page(s), live)" % (head, body, deliver(shown, nshown, jp, cp, renamed, large), time.time() - t0, pages))

def _tenant_balance(tid):
    """The ledger's own current balance (Tenants/{id} Balance); None when it cannot be read."""
    try:
        st_, h_, d_ = api("/Tenants/%d" % int(tid), {"fields": "TenantID,Balance", "embeds": "Balance"}, timeout=20)
        if st_ == 200 and isinstance(d_, dict) and d_.get("Balance") is not None: return float(d_["Balance"])
    except (ToolError, ValueError, TypeError):
        pass
    return None

def _amount_sum(path, flt):
    """Sum of Amount over a filtered ledger slice (Amount only, one call); None when too large or failing."""
    try:
        st_, h_, d_ = api(path, {"fields": "Amount", "filters": flt, "pageSize": 20000}, timeout=30)
    except ToolError:
        return None
    if st_ == 204: return 0.0
    if st_ != 200 or not isinstance(d_, list) or int(h_.get("x-total-results") or len(d_)) > len(d_): return None
    return round(sum(_num(x.get("Amount")) or 0.0 for x in d_), 2)

def ledger_opening(tid, path, ext, since, until):
    """Opening balance = what the ledger held before the first listed row, so RunningBalance is the ledger's OWN balance even when older rows
    are not listed (cap, since). None when it cannot be determined. Newest slice: current balance - rows after 'until' - listed rows."""
    capped = ext["total"] > ext["nrec"]
    if not (capped or since or until): return 0.0
    listed = round(sum(_num(f.get("Amount")) or 0.0 for f in ext["flat"]), 2)
    if ext["newest"] or not capped:
        bal = _tenant_balance(tid)
        after = _amount_sum(path, "TransactionDate,gt,%s" % until) if until else 0.0
        return None if bal is None or after is None else round(bal - after - listed, 2)
    before = _amount_sum(path, "TransactionDate,lt,%s" % since) if since else 0.0   # oldest slice: everything before the window
    return before

def _ledger_recipe(r, vals, flt, post, name, scope, common, opts):
    """tenant_ledger for one or several tenants: one call per tenant (parallel, at most 4, inside the fan-out budget), newest rows win the
    cap, RunningBalance is the ledger's own running balance (older rows that are not listed are included in it)."""
    t0 = time.time()
    qd = r["request"]["query"]
    tenants = vals.get("_tenants") or [(vals.get("tenant"), "Tenant ID %s" % vals.get("tenant"))]
    if len(tenants) > 25:
        raise ToolError("At most 25 tenants per call (got %d). For a whole property use one built-in report instead: %s" % (len(tenants), report_hint("Transactions")))
    for tid, _l in tenants: check_fanout({"ctrl": "Tenants", "id": str(tid)}, 0, False)
    limit = row_limit(vals.get("max_rows") or 1000)
    since, until = vals.get("since") or "", vals.get("until") or ""
    qd1 = dict(qd, max_records=limit)
    multi = len(tenants) > 1
    def one(t):
        ext = {}
        o2 = {}
        _query_renamed(qd1, dict(vals, tenant=t[0]), flt, post, name, scope, common, o2, ext)
        ext["cap"] = o2.get("cap") or []
        ext["open"] = ledger_opening(t[0], "/Tenants/%d/Transactions" % int(t[0]), ext, since, until) if ext.get("flat") is not None and ext.get("total") else 0.0
        return ext
    with ThreadPoolExecutor(max(1, min(4, len(tenants)))) as ex:
        exts = list(ex.map(one, tenants))
    shown, raw, caps, ends, total_all, nrec_all, unknown = [], [], [], [], 0, 0, []
    for (tid, label), e in zip(tenants, exts):
        if not e.get("shown") and not e.get("total"):
            ends.append((label, 0.0)); continue
        op = e["open"]
        if op is None: unknown.append(label)
        base = op or 0.0
        for row in e["shown"]:
            if "RunningBalance" in row and row["RunningBalance"] is not None: row["RunningBalance"] = round(row["RunningBalance"] + base, 2)
            shown.append(dict({"Tenant": label}, **row) if multi else row)
        lst = round(sum(_num(f.get("Amount")) or 0.0 for f in e["flat"]), 2)
        ends.append((label, round(base + lst, 2)))
        raw += e["raw"]; total_all += e["total"]; nrec_all += e["nrec"]
        for c in e["cap"]:
            caps.append(("%s: " % label if multi else "") + c)
        if e["cap"] and e["total"] > e["nrec"] and not e["partial"]:
            caps[-1] += (" The running balance shown is the ledger's own (opening balance %s of the older rows included); the older rows are not listed." % ("%.2f" % op if op is not None else "unavailable"))
    if unknown: caps.append("Opening balance could not be read for %s: RunningBalance there starts at 0 at the oldest row shown." % ", ".join(unknown))
    for c in caps: opts.setdefault("cap", []).append(c)
    opts["rows"] = shown
    if multi:
        opts["headline"] = "%d ledger transactions for %d tenants (newest first within each tenant); ending balance per tenant: %s." % (
            len(shown), len(tenants), "; ".join("%s %s" % (l, _money(b)) for l, b in ends))
        opts["facts"] = {"transactions": len(shown), "tenants": len(tenants), "ending_balances": {l: b for l, b in ends}, "scope": scope}
    elif ends:
        opts["facts"] = {"transactions": len(shown), "ending_balance": ends[0][1], "scope": scope}
        opts["headline"] = "%d ledger transactions (newest first unless oldest_first); ending running balance %s." % (len(shown), _money(ends[0][1]))
    if not shown and not raw:
        return "(no ledger rows for these tenants%s)\nScope: %s" % (" in that date range" if (since or until) else "", scope)
    jp, cp = save(name, raw, shown)
    head = "Tenants/{id}/Transactions: %d of %d record(s) for %d tenant(s). Scope: %s" % (nrec_all, total_all, len(tenants), scope)
    lim, large = show_plan(common["show"], len(shown))
    body, nshown = fit_table(shown, lim, None, MAX_TEXT - INLINE_RESERVE - len(head))
    return clip("%s\n%s\n%s\n(%.1fs, live)" % (head, body, deliver(shown, nshown, jp, cp, shown, large), time.time() - t0))

# ----------------------------------------------------------------------------- resolve
def t_resolve(term="", kind="property", property=""):
    kind = (kind or "property").lower().rstrip("s")
    if kind == "property":
        if not str(term).strip() or str(term).strip().lower() in ("all", "*", "list"):
            ps = properties()
            act = [p for p in ps if p.get("active", True)]
            return "%d active properties (of %d). id | name | short name:\n%s" % (
                len(act), len(ps), "\n".join("%s | %s | %s" % (p["id"], p["name"], p["short"]) for p in sorted(act, key=lambda p: p["name"].lower())))
        m = match_detail(term)
        if not m: return "No property matches '%s'. resolve('') lists all." % term
        src = "local/aliases.json" if os.path.exists(ALIASES) else "live /Properties"
        rows = "\n".join("%d | %s | %s | %s | %s: %s%s" % (sc, p["id"], p["name"], p["short"], f, t, "" if p.get("active", True) else " | INACTIVE") for sc, p, f, t in m)
        if m[0][0] < CONFIDENT:
            return ("No confident match for '%s' (best score %d < %d). Do NOT guess: ask the user which property they mean. Closest [%s] "
                    "(score | PropertyID | name | short | matched field: value):\n%s" % (term, m[0][0], CONFIDENT, src, rows))
        return "Matches for '%s' (score | PropertyID | name | short | matched field: value) [%s]:\n%s" % (term, src, rows)
    cfg = {"tenant": ("/Tenants", "TenantID,Name,PropertyID", "Name"), "unit": ("/Units", "UnitID,Name,PropertyID", "Name"),
           "owner": ("/Owners", "OwnerID,Name", "Name"), "vendor": ("/Vendors", "VendorID,Name", "Name")}
    if kind not in cfg: raise ToolError("kind must be property, tenant, unit, owner or vendor")
    path, fields, fld = cfg[kind]
    flt = "%s,ct,%s" % (fld, str(term).replace(",", " ").strip())
    if property and kind in ("tenant", "unit"):
        ids, _ = resolve_property_ids(property)
        if ids: flt += ";PropertyID,in,(%s)" % ",".join(map(str, ids))
    s, h, d = api(path, {"filters": flt, "fields": fields, "pageSize": 25})
    if s == 204 or not d: return "No %s matches '%s'." % (kind, term)
    if s != 200: raise ToolError(api_error(s, d))
    extra = ""
    if kind == "owner":   # list each matched owner's properties from the property index (no extra API call)
        try:
            ps = properties()
            lines = []
            for o in d[:25]:
                on = _norm(o.get("Name"))
                lines += ["%s | %s | %s" % (o.get("Name"), p["id"], p["name"]) for p in ps if on and on in [_norm(x) for x in p.get("owners") or []]]
            if lines: extra = "\nProperties of these owners (owner | PropertyID | name):\n" + "\n".join(lines[:100])
        except ToolError:
            pass
    return "%s matches for '%s' (%s of %s):\n%s%s" % (kind, term, len(d), h.get("x-total-results", len(d)), table([flatten(x) for x in d], 25), extra)

# ----------------------------------------------------------------------------- v11: locations
# Each Rent Manager location is a SEPARATE database: IDs (properties, units, tenants...) repeat across locations. Every tool accepts
# location=<name or LocationID>; call_tool sets a thread-local that rm.get/get_bytes turn into the x-rm12api-locationid header.
_LOCS = {"data": None}

def load_locations(force=False):
    """[{id, name, friendly, default}] from /Current/Locations (asked WITHOUT a location header, per the RM docs). Cached per process."""
    if _LOCS["data"] is None or force:
        try:
            s, h, d = rm.get("/Current/Locations", {"fields": "LocationID,Name,FriendlyName,IsMainLocation"}, loc="")
        except rm.RMError as e:
            raise ToolError(str(e))
        if s != 200 or not isinstance(d, list):
            raise ToolError("Could not list locations: %s" % api_error(s, d))
        env = str(rm.ENV.get("RM_LOCATION") or "").strip()
        main = [str(x.get("LocationID")) for x in d if x.get("IsMainLocation")]
        dflt = env or (main[0] if main else (str(d[0].get("LocationID")) if len(d) == 1 else ""))
        _LOCS["data"] = [{"id": str(x.get("LocationID")), "name": x.get("Name") or x.get("FriendlyName") or ("Location %s" % x.get("LocationID")), "friendly": x.get("FriendlyName") or "",
                          "default": str(x.get("LocationID")) == dflt} for x in d]
    return _LOCS["data"]

def resolve_location(v, locs=None):
    """Name (exact, then unique partial) or LocationID -> location dict; unknown -> ToolError listing the choices."""
    locs = locs if locs is not None else load_locations()
    t = str(v).strip()
    hit = [l for l in locs if l["id"] == t]
    if not hit:
        hit = [l for l in locs if t.lower() in (l["name"].lower(), l["friendly"].lower())]
    if not hit and t:
        hit = [l for l in locs if t.lower() in l["name"].lower() or t.lower() in l["friendly"].lower()]
        if len(hit) > 1:
            raise ToolError("Location '%s' is ambiguous: %s" % (t, "; ".join("%s (ID %s)" % (l["name"], l["id"]) for l in hit)))
    if not hit:
        raise ToolError("Unknown location '%s'. Locations: %s. (Each location is a separate database.)" % (
            t, "; ".join("%s (ID %s)%s" % (l["name"], l["id"], ", default" if l["default"] else "") for l in locs) or "(none)"))
    return hit[0]

def t_locations():
    t0 = time.time()
    locs = load_locations(force=True)
    def probe(l):
        try:
            s, h, d = rm.get("/Properties", {"fields": "PropertyID", "pageSize": 1}, loc=l["id"])
        except Exception as e:
            return "unknown (%s)" % type(e).__name__
        return "yes" if s in (200, 204) else ("NO (HTTP %s: this API user has no access)" % s if s in (401, 403) else "unknown (HTTP %s)" % s)
    with ThreadPoolExecutor(min(4, max(1, len(locs)))) as ex:
        acc = list(ex.map(probe, locs[:30]))
    lines = ["LocationID|name|default|accessible"]
    for l, a in zip(locs, acc):
        lines.append("%s|%s%s|%s|%s" % (l["id"], l["name"], " (%s)" % l["friendly"] if l["friendly"] and l["friendly"] != l["name"] else "", "yes" if l["default"] else "", a))
    lines.append("Each location is a separate database: PropertyID/UnitID/TenantID values repeat across locations. Pass location='<name or id>' to any tool; "
                 "omit it for the default. Scope lines name a non-default location.")
    lines.append("Scope: locations visible to this API user (%d)\n(%.1fs, live)" % (len(locs), time.time() - t0))
    return "\n".join(lines)

def tag_location(text, loc):
    """Name a non-default location in every 'Scope:' (or append a Location line)."""
    text = str(text)
    tag = "[Location %s, ID %s]" % (loc["name"], loc["id"])
    if "Scope: " in text:
        return text.replace("Scope: ", "Scope: %s " % tag)
    return text + "\nScope: %s" % tag

# ----------------------------------------------------------------------------- v11: attachments (find_attachments / download_file)
HIST_SOURCES = {"tenant_history": "Tenants", "unit_history": "Units", "owner_history": "Owners", "vendor_history": "Vendors",
                "service_issue": "ServiceManagerIssues", "property_history": "Properties"}
UDF_PARENTS = {"tenant": "Tenants", "property": "Properties", "unit": "Units", "owner": "Owners", "vendor": "Vendors", "service_issue": "ServiceManagerIssues"}
SOURCES = list(HIST_SOURCES) + ["lease", "property_files", "custom_field"]
ATT_COLS = ["file_ref", "source", "parent_id", "item_id", "name", "ext", "description", "created", "item_date", "category", "size", "note"]
TEXT_EXT = {"txt", "csv", "tsv", "json", "xml", "html", "htm", "md", "eml", "log"}
INLINE_CAP = 20000      # characters of text returned inline
PDF_CAP = 6000          # characters of PDF text returned
_HISTCATS = {}

def parse_ids(ids, what="ids", cap=None):
    cap = cap or FANOUT_BUDGET
    if isinstance(ids, bool): raise ToolError("%s must be numbers" % what)
    if isinstance(ids, (int, float)): ids = [ids]
    if isinstance(ids, str): ids = re.findall(r"-?\d+", ids)
    try:
        out = [int(x) for x in (ids or [])]
    except (TypeError, ValueError):
        raise ToolError("%s must be numbers (e.g. [123, 456])" % what)
    out = list(dict.fromkeys(out))
    if not out: raise ToolError("%s is required: one or more parent ids" % what)
    if len(out) > cap: raise ToolError("At most %d %s per call (fan-out budget); split the list." % (cap, what))
    return out

def hist_categories():
    """{HistoryCategoryID: Name} for the current location (one call, cached)."""
    key = rm.current_location() or ""
    if key not in _HISTCATS:
        rows, total, pages, s = fetch("/HistoryCategories", {"fields": "HistoryCategoryID,Name"}, max_records=2000)
        _HISTCATS[key] = {int(x["HistoryCategoryID"]): x.get("Name") or "" for x in (rows if isinstance(rows, list) else [])}
    return _HISTCATS[key]

def category_ids(cat):
    """'Lease, Rider' or '3,6' -> [ids]. Exact name wins, else partial matches. Unknown -> ToolError with the names."""
    cats = hist_categories()
    out = []
    for it in [x.strip() for x in str(cat).split(",") if x.strip()]:
        if it.isdigit():
            out.append(int(it)); continue
        ex = [i for i, n in cats.items() if n.lower() == it.lower()]
        part = ex or [i for i, n in cats.items() if it.lower() in n.lower()]
        if not part:
            raise ToolError("Unknown history category '%s'. Names: %s" % (it, ", ".join(sorted(cats.values())[:80])))
        out += part
    return list(dict.fromkeys(out))

def _ext(e):
    return str(e or "").strip().lstrip(".").lower()

def att_row(ref, source, parent, item, f, created="", item_date="", category="", size="", note="", desc=""):
    f = f or {}
    return {"file_ref": ref, "source": source, "parent_id": parent, "item_id": item, "name": f.get("Name") or "", "ext": _ext(f.get("Extension")),
            "description": desc or f.get("Description") or "", "created": str(created or f.get("CreateDate") or "")[:19].replace("T", " "),
            "item_date": str(item_date or "")[:10], "category": category or "", "size": size if size not in (None, 0, -1) else "",
            "note": re.sub(r"\s+", " ", str(note or "")).strip()[:100]}

_HIST_FIELDS = ("HistoryID,HistoryDate,Note,FileID,HistoryCategoryID,HistoryCategory.Name,Attachment.FileID,Attachment.Name,Attachment.Extension,"
                "Attachment.Description,Attachment.CreateDate,HistoryAttachments.FileID,HistoryAttachments.File.FileID,HistoryAttachments.File.Name,"
                "HistoryAttachments.File.Extension,HistoryAttachments.File.Description,HistoryAttachments.File.CreateDate")

def _hist_rows(source, pid, flt):
    ctrl = HIST_SOURCES[source]
    params = {"filters": ";".join(["FileID,gt,0"] + flt), "embeds": "Attachment,HistoryAttachments.File,HistoryCategory", "fields": _HIST_FIELDS}
    rows, total, pages, s = fetch("/%s/%d/HistoryNotes" % (ctrl, pid), params, max_records=3000)
    out = []
    for n in (rows if isinstance(rows, list) else []):
        cat = (n.get("HistoryCategory") or {}).get("Name") or ""
        seen = set()
        files = []
        a = n.get("Attachment") or {}
        if (a.get("FileID") or 0) > 0: files.append(a)
        for h in n.get("HistoryAttachments") or []:
            f = h.get("File") or {}
            if (f.get("FileID") or 0) > 0: files.append(f)
        for f in files:
            if f["FileID"] in seen: continue
            seen.add(f["FileID"])
            out.append(att_row("history:%s:%s" % (n.get("HistoryID"), f["FileID"]), source, pid, n.get("HistoryID"), f,
                               item_date=n.get("HistoryDate"), category=cat, note=n.get("Note")))
    return out, 1

def _lease_rows(ids, by):
    key = "UnitID" if by == "unit" else "ParentLeaseID"
    chunks = [ids[i:i + 40] for i in range(0, len(ids), 40)]
    def one(c):
        params = {"filters": "%s,in,(%s)" % (key, ",".join(str(i) for i in c)), "embeds": "LeaseRenewalAttachments",
                  "fields": "LeaseRenewalID,ParentLeaseID,UnitID,StartDate,EndDate,LeaseRenewalAttachments"}
        return fetch("/LeaseRenewals", params, max_records=5000)[0]
    with ThreadPoolExecutor(min(4, len(chunks))) as ex:
        parts = list(ex.map(one, chunks))
    out, nren, noatt = [], 0, 0
    for part in parts:
        for lr in (part if isinstance(part, list) else []):
            nren += 1
            atts = lr.get("LeaseRenewalAttachments") or []
            if not atts: noatt += 1
            term = "term %s to %s" % (str(lr.get("StartDate") or "?")[:10], str(lr.get("EndDate") or "?")[:10])
            for a in atts:
                f = a.get("File") or {}
                fid = f.get("FileID") or a.get("FileID")
                if not fid: continue
                out.append(att_row("leaserenewal:%s:%s" % (lr.get("LeaseRenewalID"), fid), "lease", lr.get("ParentLeaseID"), lr.get("LeaseRenewalID"),
                                   f, category=str(a.get("LeaseAttachmentType") or ""), note="%s; unit %s" % (term, lr.get("UnitID"))))
    return out, nren, noatt

def _pfile_rows(pid):
    params = {"embeds": "File,PropertyFolder", "fields": "PropertyFileID,PropertyID,FileID,Title,Description,FileSize,CreateDate,PropertyFolder.Name,"
              "File.FileID,File.Name,File.Extension,File.Description,File.CreateDate"}
    rows, total, pages, s = fetch("/Properties/%d/PropertyFiles" % pid, params, max_records=3000)
    out = []
    for p in (rows if isinstance(rows, list) else []):
        f = dict(p.get("File") or {})
        fid = f.get("FileID") or p.get("FileID")
        if not fid or fid < 0: continue
        f.setdefault("Name", p.get("Title") or "")
        out.append(att_row("propertyfile:%s:%s" % (pid, fid), "property_files", pid, p.get("PropertyFileID"), f, created=p.get("CreateDate") or f.get("CreateDate"),
                           category=(p.get("PropertyFolder") or {}).get("Name") or "", size=p.get("FileSize"),
                           desc=p.get("Description") or p.get("Title") or f.get("Description") or ""))
    return out

def _udf_rows(parent, pid):
    ctrl = UDF_PARENTS[parent]
    params = {"filters": "Attachment.FileID,gt,0", "embeds": "Attachment", "fields": "UserDefinedValueID,UserDefinedFieldID,ParentID,Name,CreateDate,"
              "Attachment.FileID,Attachment.Name,Attachment.Extension,Attachment.Description,Attachment.CreateDate"}
    rows, total, pages, s = fetch("/%s/%d/UserDefinedValues" % (ctrl, pid), params, max_records=3000)
    out = []
    for v in (rows if isinstance(rows, list) else []):
        f = v.get("Attachment") or {}
        if (f.get("FileID") or 0) <= 0: continue
        out.append(att_row("udf_%s:%s:%s" % (parent, pid, f["FileID"]), "custom_field", pid, v.get("UserDefinedValueID"), f, category=v.get("Name") or "",
                           created=f.get("CreateDate") or v.get("CreateDate")))
    return out

def att_cell(v):
    return str(v if v is not None else "").replace("\r", " ").replace("\n", " ").replace("|", "/")

def t_find_attachments(source, ids, parent="", category="", name_contains="", extension="", start="", end="", limit=100):
    t0 = time.time()
    src = str(source or "").strip().lower().replace("-", "_").replace(" ", "_")
    if src not in SOURCES:
        raise ToolError("source must be one of: %s" % ", ".join(SOURCES))
    parent = str(parent or "").strip().lower()
    if src == "custom_field":
        parent = parent or "tenant"
        if parent not in UDF_PARENTS: raise ToolError("For custom_field give parent=%s" % "|".join(UDF_PARENTS))
    elif src == "lease":
        parent = parent or "lease"
        if parent not in ("lease", "unit"): raise ToolError("For lease, ids are LeaseIDs (parent='lease', default) or UnitIDs (parent='unit').")
    idl = parse_ids(ids, "ids", cap=400 if src == "lease" else None)
    flt = []
    cat_ids = []
    if src in HIST_SOURCES:
        if category: cat_ids = category_ids(category); flt.append("HistoryCategoryID,in,(%s)" % ",".join(str(i) for i in cat_ids))
        if start: flt.append("HistoryDate,ge,%s" % parse_date(start))
        if end: flt.append("HistoryDate,le,%s" % parse_date(end, end=True))
    notes, errors = [], []
    rows, scanned = [], len(idl)
    def one(i):
        try:
            if src in HIST_SOURCES: return i, _hist_rows(src, i, flt)[0], None
            if src == "property_files": return i, _pfile_rows(i), None
            return i, _udf_rows(parent, i), None
        except ToolError as e:
            return i, [], str(e)
    if src == "lease":
        rows, nren, noatt = _lease_rows(idl, parent)
        notes.append("%d lease renewal row(s) found, %d without any attachment (the file may sit on an earlier renewal; check every row)" % (nren, noatt))
    else:
        with ThreadPoolExecutor(min(4, len(idl))) as ex:
            for i, rr, err in ex.map(one, idl):
                rows += rr
                if err: errors.append("id %s: %s" % (i, err[:160]))
        if errors and len(errors) == len(idl): raise ToolError("All %d id(s) failed. First: %s" % (len(idl), errors[0]))
    # local filters (the API cannot filter these everywhere)
    if category and src not in HIST_SOURCES:
        cl = [c.strip().lower() for c in str(category).split(",") if c.strip()]
        rows = [r for r in rows if any(c in r["category"].lower() for c in cl)]
    if name_contains:
        nc = str(name_contains).lower()
        rows = [r for r in rows if nc in (r["name"] + " " + r["description"] + " " + r["note"]).lower()]
    if extension:
        ex_ = {_ext(e) for e in str(extension).split(",") if e.strip()}
        rows = [r for r in rows if r["ext"] in ex_]
    if src not in HIST_SOURCES and (start or end):
        a_, b_ = (parse_date(start) if start else ""), (parse_date(end, end=True) if end else "9999")
        rows = [r for r in rows if a_ <= (r["created"] or r["item_date"])[:10] <= b_]
    rows.sort(key=lambda r: (r["created"] or r["item_date"]), reverse=True)
    n = max(1, int(limit or 100))
    shown = rows[:n]
    parents = len({(r["parent_id"]) for r in rows})
    loc = rm.current_location()
    scope = "%s ids %s%s%s" % (src + (" (by %s)" % parent if parent else ""), ",".join(str(i) for i in idl[:10]), ",... (%d ids)" % len(idl) if len(idl) > 10 else "",
                               "; filters: " + ", ".join(x for x in ("category=%s" % category if category else "", "name~%s" % name_contains if name_contains else "",
                                                                  "ext=%s" % extension if extension else "", "from %s" % start if start else "", "to %s" % end if end else "") if x) if (category or name_contains or extension or start or end) else "")
    head = "find_attachments: %d file(s) for %d of %d id(s)%s. file_ref goes to download_file; rows newest first; the signed download links are not shown." % (
        len(rows), parents, scanned, "; " + "; ".join(notes) if notes else "")
    if errors: head += "\nERRORS (those ids were skipped): " + " | ".join(errors[:5])
    if len(rows) > n: head += "\nShowing %d of %d (raise limit, or filter by category/name_contains/extension/start/end)." % (n, len(rows))
    body = "\n".join(["|".join(ATT_COLS)] + ["|".join(att_cell(r.get(c)) for c in ATT_COLS) for r in shown]) if shown else "(no files found)"
    extra = ""
    if len(rows) > n:
        jp, cp = save("find_attachments", rows, rows)
        extra = "\nAll rows saved (CSV):\n" + file_lines(cp or jp)
    return "%s\n%s%s\nScope: %s (live; each location is a separate database)\n(%.1fs, %d call(s) max)" % (head, body, extra, scope, time.time() - t0, len(idl))

# ---- download
def files_dir():
    return os.path.join(out_dir(), "files")

def safe_name(name, fallback="file"):
    """File name from untrusted text: last path part only, letters/digits/._- , no leading dots, no Windows device names, max 80 chars."""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" ._")[:80]
    if not base or base.upper().split(".")[0] in ("CON", "PRN", "AUX", "NUL") or re.fullmatch(r"(COM|LPT)\d", base.upper().split(".")[0] or ""):
        base = ("_" + base) if base else fallback
    return base

def reserve_file(label, ext, folder=None):
    """Collision-safe exclusive create in out/files/ (never overwrites). Returns the path of the new empty file."""
    d = folder or files_dir()
    os.makedirs(d, exist_ok=True)
    ext = re.sub(r"[^a-z0-9]", "", str(ext).lower())[:8] or "bin"
    base = safe_name(label)
    if base.lower().endswith("." + ext): base = base[: -(len(ext) + 1)]
    n = 0
    while True:
        fp = os.path.join(d, "%s%s.%s" % (base, "" if n == 0 else "-%d" % (n + 1), ext))
        try:
            open(fp, "xb").close(); return fp
        except FileExistsError:
            n += 1

_STAMPED = re.compile(r"_\d{8}-\d{6}(?:-\d+)?\.[A-Za-z0-9]+$")

def prune_downloads(days=None, base=None, now=None):
    """Delete OLD files this program created: regular files directly in out/ whose name has our _YYYYMMDD-HHMMSS stamp (query/report results,
    PDFs) and any regular file directly in out/files/ (downloads). No deeper folders, no links, nothing else. Age = modified time; older than
    RM_OUT_KEEP_DAYS (default 14; 0 = keep forever). Returns the number removed."""
    try:
        days = float(os.environ.get("RM_OUT_KEEP_DAYS", "14")) if days is None else float(days)
    except ValueError:
        days = 14.0
    if days <= 0: return 0
    top = base or out_dir()
    cutoff = (now or time.time()) - days * 86400
    n = 0
    for d, only_stamped in ((top, True), (os.path.join(top, "files"), False)):
        if not os.path.isdir(d): continue
        for nm in os.listdir(d):
            fp = os.path.join(d, nm)
            try:
                if (os.path.isfile(fp) and not os.path.islink(fp) and (not only_stamped or _STAMPED.search(nm))
                        and os.path.getmtime(fp) < cutoff):
                    os.remove(fp); n += 1
            except OSError:
                pass
    return n

_PRUNE = {"t": 0.0}

def maybe_prune(now=None):
    """Prune at most once per 24 h per process (called at start and on every tool call)."""
    t = now or time.time()
    if t - _PRUNE["t"] < 86400: return 0
    _PRUNE["t"] = t
    try: return prune_downloads()
    except Exception: return 0

_REF = re.compile(r"^([a-z_]+):(\d+):(\d+)$")

def parse_ref(ref):
    """'history:<HistoryID>:<FileID>' | 'leaserenewal:<LeaseRenewalID>:<FileID>' | 'propertyfile:<PropertyID>:<FileID>' | 'udf_<parent>:<ParentID>:<FileID>'
    -> (kind, container id, FileID, endpoint path, params). Never calls the API."""
    m = _REF.match(str(ref or "").strip().lower())
    if not m: raise ToolError("Bad file_ref '%s'. Use a file_ref from find_attachments (e.g. history:123:456)." % str(ref)[:60])
    kind, cid, fid = m.group(1), int(m.group(2)), int(m.group(3))
    if kind == "history":
        return kind, cid, fid, "/History/%d/Attachments" % cid, {}
    if kind == "leaserenewal":
        return kind, cid, fid, "/LeaseRenewals", {"filters": "LeaseRenewalID,eq,%d" % cid, "embeds": "LeaseRenewalAttachments"}
    if kind == "propertyfile":
        return kind, cid, fid, "/Properties/%d/PropertyFiles" % cid, {"embeds": "File"}
    if kind.startswith("udf_") and kind[4:] in UDF_PARENTS:
        return kind, cid, fid, "/%s/%d/UserDefinedValues" % (UDF_PARENTS[kind[4:]], cid), {"embeds": "Attachment", "filters": "Attachment.FileID,eq,%d" % fid}
    raise ToolError("Unknown file_ref kind '%s'." % kind)

def _find_file(o, fid):
    """The File object with FileID == fid and a DownloadURL, anywhere inside an API reply."""
    if isinstance(o, dict):
        if o.get("FileID") == fid and o.get("DownloadURL"): return o
        for v in o.values():
            r = _find_file(v, fid)
            if r: return r
    elif isinstance(o, list):
        for v in o:
            r = _find_file(v, fid)
            if r: return r
    return None

def resolve_ref(ref):
    """file_ref -> fresh File dict (DownloadURL included) from the route that lists it. ToolError if gone."""
    kind, cid, fid, path, params = parse_ref(ref)
    s, h, d = api(path, params)
    if s == 204 or (s == 200 and not d):
        raise ToolError("file_ref %s: the item has no files now (HTTP %s). Run find_attachments again; check location=." % (ref, s))
    if s != 200: raise ToolError("file_ref %s: %s" % (ref, api_error(s, d)))
    f = _find_file(d, fid)
    if not f: raise ToolError("file_ref %s: file %d not found under that item any more, or it has no download link." % (ref, fid))
    return f

def pdf_text(src, maxchars=PDF_CAP, max_pages=15):
    """Plain text of the first pages of a PDF (bytes or path) when pdfplumber or pypdf is importable; (text, note). Never raises."""
    import io
    try:
        data = src if isinstance(src, (bytes, bytearray)) else open(src, "rb").read(60 * 1024 * 1024)
    except OSError:
        return "", "could not read the file for text extraction"
    txt, lib = "", None
    try:
        import pdfplumber
        lib = "pdfplumber"
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pg in pdf.pages[:max_pages]:
                txt += (pg.extract_text() or "") + "\n"
                if len(txt) > maxchars: break
    except ImportError:
        try:
            import pypdf
            lib = "pypdf"
            rd = pypdf.PdfReader(io.BytesIO(data))
            for pg in rd.pages[:max_pages]:
                txt += (pg.extract_text() or "") + "\n"
                if len(txt) > maxchars: break
        except ImportError:
            return "", "no PDF text library installed (pdfplumber or pypdf), so no text was extracted"
        except Exception as e:
            return "", "PDF text extraction failed (%s)" % type(e).__name__
    except Exception as e:
        return "", "PDF text extraction failed (%s)" % type(e).__name__
    txt = txt.strip()
    if len(txt) < 20:
        return "", "no text found in the first pages (%s): this PDF is probably a scan (images only); a person has to read it" % lib
    return txt[:maxchars], "text from the first pages via %s%s" % (lib, "; cut at %d characters" % maxchars if len(txt) > maxchars else "")

def _download_one(ref="", url="", mode="auto", save_as="", return_text=True, cap=INLINE_CAP):
    """One file -> text block. mode auto|text|link|save. Raises ToolError."""
    mode = (mode or "auto").lower()
    if mode not in ("auto", "text", "link", "save"): raise ToolError("mode must be auto, text, link or save")
    meta = {}
    if ref:
        meta = resolve_ref(ref)
        url = meta.get("DownloadURL")
    elif url:
        try: rm.check_file_url(url)
        except rm.RMError as e: raise ToolError(str(e))
    else:
        raise ToolError("give file_ref (from find_attachments) or download_url")
    name = meta.get("Name") or ""
    ext = _ext(meta.get("Extension")) or (os.path.splitext(name)[1].lstrip(".").lower() if name else "")
    if mode == "auto": mode = "text" if ext in TEXT_EXT else "save"
    info = "%s%s | FileID %s" % (name or "(unnamed)", " (.%s)" % ext if ext else "", meta.get("FileID", "?"))
    if mode == "link":
        return ("%s\nDownloadURL: %s\nThis link is a signed capability URL: anyone holding it can download the file. Treat it like a password: do not paste it into "
                "issues, chats or logs. Nothing was downloaded or saved." % (info, url))
    try:
        if mode == "text":
            st, h, data, size, sha = rm.fetch_file(url)
        else:
            st = None
    except rm.RMError as e:
        raise ToolError("%s: %s%s" % (info, e, " (use mode='save' for big files)" if "limit" in str(e) else ""))
    if mode == "text":
        if st != 200: raise ToolError("%s: download failed (HTTP %s)" % (info, st))
        ctype = h.get("content-type", "")
        line = "%s | %d bytes | %s | sha256 %s | saved: nothing (mode=text)" % (info, size, ctype or "?", sha)
        if data[:4] == b"%PDF":
            if not return_text: return line
            t, note = pdf_text(data)
            return "%s\n%s%s" % (line, note, ("\n--- text ---\n" + t) if t else "") + ("" if t else "\nNot saved. Use mode='save' to keep the PDF for the user to open.")
        if ext in TEXT_EXT or ctype.startswith("text/"):
            if not return_text: return line
            txt = data.decode("utf-8", "replace")
            return "%s\n--- text%s ---\n%s" % (line, " (first %d of %d characters)" % (cap, len(txt)) if len(txt) > cap else "", txt[:cap])
        return "%s\nBinary file (not text): nothing returned. Use mode='save' to save it to out/files/ for the user to open." % line
    # save
    fid = meta.get("FileID")
    label = save_as or ((os.path.splitext(safe_name(name))[0] + ("_%s" % fid if fid else "")) if name else "attachment%s" % ("_%s" % fid if fid else ""))
    fp = reserve_file(label, ext or "bin")
    try:
        st, h, _, size, sha = rm.fetch_file(url, dest=fp)
        if st != 200 or sha is None:
            raise ToolError("%s: download failed (HTTP %s)" % (info, st))
    except rm.RMError as e:
        _rm_empty(fp)
        raise ToolError("%s: %s" % (info, e))
    except ToolError:
        _rm_empty(fp)
        raise
    ctype = h.get("content-type", "")
    out = ["%s | %d bytes | %s | sha256 %s" % (info, size, ctype or "?", sha), file_lines(fp),
           "Cowork/Claude Code models can read it from this path; Desktop chat models cannot. "
           "It contains tenant data. Files in out/files/ are deleted after %s days." % (os.environ.get("RM_OUT_KEEP_DAYS", "14") or "14")]
    with open(fp, "rb") as f: magic = f.read(4)
    if magic == b"%PDF" and return_text:
        t, note = pdf_text(fp)
        out.append(note + (":\n--- text ---\n" + t if t else ""))
    return "\n".join(out)

def _rm_empty(fp):
    """Remove the empty placeholder this call created (only if still empty)."""
    try:
        if os.path.isfile(fp) and os.path.getsize(fp) == 0: os.remove(fp)
    except OSError:
        pass

def t_download_file(file_ref=None, download_url="", mode="auto", save_as="", return_text=True):
    t0 = time.time()
    refs = file_ref if isinstance(file_ref, (list, tuple)) else ([file_ref] if file_ref else [])
    refs = [str(r).strip() for r in refs if str(r).strip()]
    if len(refs) > 20: raise ToolError("At most 20 files per call.")
    if not refs and not download_url: raise ToolError("give file_ref (from find_attachments; a list is allowed, max 20) or download_url")
    if len(refs) <= 1:
        res = _download_one(refs[0] if refs else "", "" if refs else download_url, mode, save_as, return_text)
        return "%s\nScope: file %s (live; mode=%s)\n(%.1fs)" % (res, refs[0] if refs else "raw rentmanager.com URL", mode or "auto", time.time() - t0)
    def one(r):
        try: return "%s -> %s" % (r, _download_one(r, "", mode, "", return_text, cap=1500).replace("\n", "\n    "))
        except ToolError as e: return "%s -> FAILED: %s" % (r, e)
    with ThreadPoolExecutor(min(4, len(refs))) as ex:
        res = list(ex.map(one, refs))
    return "%d file(s) requested; text-like files are shown (first 1500 characters), binaries saved to out/files/:\n%s\nScope: %d file_ref(s) (live; mode=%s)\n(%.1fs)" % (
        len(refs), "\n".join(res), len(refs), mode or "auto", time.time() - t0)

# ----------------------------------------------------------------------------- MCP plumbing
S = lambda d: {"type": "string", "description": d}
I = lambda d: {"type": "integer", "description": d}
B = lambda d: {"type": "boolean", "description": d}
POST = {"columns": S("Comma list of columns to keep/order (after flattening, e.g. Property.Name)"),
        "group_by": S("Column(s) to group by; gives count + sums"), "sum": S("Column(s) to total"),
        "sort": S("Column(s) to sort by; prefix - for descending"), "top": I("Keep only the first N rows (after sort)"),
        "where": S("Local row filter 'Column,op,value' (eq ne gt ge lt le ct)"), "show": I("Rows to show inline (default 40; 10 when the result has over 1000 rows); replies are size-capped, page the rest with read_result")}

TOOLS = [
    ("find_recipe", "Rent Manager: find a ready-made recipe for a question about tenants, units, delinquency, vacancy, rent roll, lease expirations, ledgers, P&L/NOI. Optional if the run_recipe catalog already shows a fit.",
     {"query": S("The user's question or key words")}, ["query"], t_find_recipe),
    ("run_recipe", "Rent Manager: run a ready-made recipe by name (delinquency, vacancy, rent roll, P&L, ledgers...).",
     {"name": S("Recipe name from the catalog"), "params": {"type": "object", "description": "e.g. {\"property\": \"Oak Court\", \"period\": \"last month\"}; recipe params may also be given at top level"},
      "show": I("Rows to show inline (default 40; 10 when over 1000 rows); page the rest with read_result")}, ["name"], t_run_recipe),
    ("resolve", "Rent Manager: turn a property name/nickname into a PropertyID (term='' lists all properties); kind=tenant|unit|owner|vendor searches those by name, live.",
     {"term": S("Name, nickname, or part of it"), "kind": S("property (default), tenant, unit, owner, vendor"),
      "property": S("Optional property to narrow tenant/unit search")}, [], t_resolve),
    ("search_models", "Rent Manager: when no recipe fits, find API resources (tables such as tenants, units, charges, work orders) for some words; also lists related reports and recipes.",
     {"query": S("Words like 'work orders' or 'tenant payments'"), "limit": I("Max results (8)")}, ["query"], t_search_models),
    ("describe_model", "Rent Manager: show a resource's fields, filters, embeds and sub-resources. section=summary|fields|filters|embeds|subs; contains= narrows.",
     {"model": S("Resource or model, e.g. Units, Tenant, Tenants/OpenReceivables"), "section": S("summary (default), fields, filters, embeds, subs"),
      "contains": S("Only names containing this text")}, ["model"], t_describe_model),
    ("enum_values", "Rent Manager: list the allowed values of an enum type (names that start with 'e', e.g. eLeasePeriod).",
     {"name": S("Enum name")}, ["name"], t_enum_values),
    ("count", "Rent Manager: fast record count (1 API call) for tenants, units, charges etc. Use before query to see how big a pull is.",
     {"resource": S("e.g. Units, Tenants, Leases"), "filters": S("Field,op,value;Field2,op,value")}, ["resource"], t_count),
    ("query", "Rent Manager: live read-only query of one resource (tenants, units, leases, rent, transactions...) with validated filters/fields/embeds; can group/sum locally. BATCH ids in one call: UnitID,in,(1,2,3), never one call per unit/tenant. Saves JSON+CSV to out/.",
     dict({"resource": S("Units | Tenants/123/Transactions | Tenants/123"), "filters": S("Field,op,value;... ops eq ne gt ge lt le in ni ct sw ew bt"),
           "fields": S("Comma list; dotted for embedded (Property.Name) - embeds are added automatically"),
           "embeds": S("Comma list of embeds"), "orderby": S("Field or -Field"), "max_records": I("Max rows (default 5000 = RM_MAX_ROWS; up to 20000). Newest rows are kept; a CAPPED line says what is missing"),
           "page_size": I("Optional page size"), "parallel": I("Parallel page requests (default 4)"), "save_as": S("Output file base name"),
           "allow_fanout": B("Only if the user insists: lift the per-record call budget (prefer a report or one bulk query)"),
           "most_recent_first": B("Newest rows first by the date column (keeps the NEWEST rows; use for ledgers/leases)"),
           "first_per": S("After sorting keep only the first row per this column, e.g. orderby=-MoveOutDate + first_per=UnitID = newest lease per unit"),
           "summary": S("rent (on RecurringCharges: tenant+subsidy portions and total per tenant) | ledger (on Tenants/{id}/Transactions: last rent charge, last payment, balance)"),
           "as_of": S("With summary=rent: rent in effect on this date (default: latest per tenant)")}, **POST),
     ["resource"], t_query),
    ("list_reports", "Rent Manager: search the built-in reports (aged receivables, rent roll, vacancy, P&L, GL...) by words, or list them.",
     {"search": S("Words, e.g. 'rent roll'"), "limit": I("Max results (20)")}, [], t_list_reports),
    ("report_info", "Rent Manager: parameters (keys, defaults, allowed values) of a built-in report.",
     {"id": S("Report ID or name")}, ["id"], t_report_info),
    ("run_report", "Rent Manager: run a built-in report live (ONE call; all properties unless property= is given). Returns the main grid; JSON saved to out/.",
     dict({"id": S("Report ID or name"), "parameters": S("Key,Value;Key2,Value2 (see report_info)"),
           "property": S("Property name(s)/ID(s), comma separated; empty = all properties"),
           "grid": S("Which grid to show (default: biggest)"), "save_as": S("Output file base name")}, **POST),
     ["id"], t_run_report),
    ("find_attachments", "Rent Manager: find files stored in Rent Manager (lease PDFs, riders, tenant files) for parent ids. source: tenant_history|unit_history|owner_history|vendor_history|service_issue|property_history (history notes, one id each; filter by category name/id such as Lease, Rider, Renewal), lease (LeaseIDs, or parent='unit' with UnitIDs; lease-renewal attachments: a lease PDF often sits on an EARLIER renewal), property_files, custom_field (parent=tenant|property|unit|owner|vendor|service_issue). Returns one row per file with file_ref for download_file; nothing is downloaded. Max 50 ids (lease 400); ids are location-specific.",
     {"source": S("tenant_history|unit_history|owner_history|vendor_history|service_issue|property_history|lease|property_files|custom_field"),
      "ids": {"anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "array", "items": {"type": ["integer", "string"]}}], "description": "Parent id(s): TenantIDs, LeaseIDs, PropertyIDs..."},
      "parent": S("custom_field: tenant|property|unit|owner|vendor|service_issue; lease: lease (default) or unit"),
      "category": S("History category name(s) or id(s), e.g. 'Lease,Rider'"), "name_contains": S("Text in file name, description or note"),
      "extension": S("e.g. pdf or pdf,docx"), "start": S("Earliest date"), "end": S("Latest date"), "limit": I("Rows shown (100)")}, ["source", "ids"], t_find_attachments),
    ("download_file", "Rent Manager: get a file found by find_attachments. mode=auto: text-like files (txt csv json xml html eml md) come back as text; PDFs/images/docs are saved to out/files/ plus extracted PDF text when a PDF library exists. Use the saved path with local file tools if this client has them; otherwise give the user the path. text: in memory only, nothing saved; link: current signed DownloadURL (secret, never post it); save: always save. file_ref may be a list (max 20).",
     {"file_ref": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}], "description": "file_ref(s) from find_attachments"},
      "download_url": S("Alternative: a rentmanager.com download link"), "mode": S("auto (default) | text | link | save"),
      "save_as": S("Saved file name (no path)"), "return_text": B("Return text of text/PDF files (default true)")}, [], t_download_file),
    ("locations", "Rent Manager: list the locations (separate databases, IDs repeat) this API user can use; every tool takes location=<name or id>.",
     {}, [], t_locations),
    ("report_pdf", "Rent Manager: PDF of a built-in report (PDF rent roll, PDF aged receivables...), one PDF file per property, saved to out/ for the USER to open. To answer questions about a report's content use run_recipe/run_report data, not the PDF.",
     {"report": S("Report ID or name, e.g. 67 (RentRoll)"),
      "properties": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "array", "items": {"type": ["string", "integer"]}}], "description": "Property names/IDs (list, comma string or one ID); one PDF each; empty = one portfolio-wide PDF"},
      "parameters": S("Other report options Key,Value;Key2,Value2 (see report_info)"), "save_as": S("File name prefix")}, ["report"], t_report_pdf),
    ("read_result", "Rent Manager: page through the full table of an earlier reply that ended with a MORE line (its result_id). Returns CSV rows from offset, whole rows up to max_chars; the last line gives the next offset or says all rows were delivered. Reads the saved result; nothing is re-queried.",
     {"result_id": S("result_id from the MORE line"), "offset": I("First row, 0-based (the MORE/Next line gives it)"),
      "limit": I("Max rows in this page (default: as many as fit max_chars)"), "columns": S("Comma list of columns to return (default all)"),
      "max_chars": I("Page size in characters (default 20000, max 60000)")}, ["result_id"], t_read_result),
]

LOCATION_ARG = S("Location name or ID (separate database); omit = default; see locations")
HIDDEN = {"page_size", "parallel", "save_as"}   # 1h: accepted (server defaults) but not advertised to the model

TITLES = {"find_recipe": "Find recipe", "run_recipe": "Run recipe", "resolve": "Resolve names to IDs", "search_models": "Search API resources",
          "describe_model": "Describe resource", "enum_values": "Enum values", "count": "Count records", "query": "Query resource",
          "list_reports": "List reports", "report_info": "Report parameters", "run_report": "Run report", "report_pdf": "Report as PDF",
          "find_attachments": "Find attachments", "download_file": "Download file", "locations": "List locations", "read_result": "Read saved result"}
LOCAL_ONLY = {"describe_model", "enum_values", "search_models", "list_reports", "report_info", "read_result"}  # no Rent Manager call
NO_LOCATION = {"read_result"}   # reads this server's saved result; a location means nothing there

def tool_desc(name, desc):
    if name == "run_recipe":
        head = ("Rent Manager: run a ready-made recipe (tenants, delinquency, vacancy, units, rent roll, P&L/NOI, ledgers, GL). "
                "If one fits, call it directly; find_recipe is optional. Params go in params or at top level; property takes a name or ID; "
                "date-range recipes take period (this_month|last_month|ytd|t12|2026-09|'September 2026'|'Q3 2026'|2025). Report recipes take report_params='Key,Value;..' for other report options.\nRecipes:\n")
        return head + recipe_catalog(DESC_LIMIT - len(head) - 20)
    return desc

def tool_list():
    out = []
    for n, d, p, r, _ in TOOLS:
        props = {k: v for k, v in p.items() if k not in HIDDEN}
        if n not in NO_LOCATION: props["location"] = LOCATION_ARG
        schema = {"type": "object", "properties": props, "required": r}
        if n == "run_recipe": schema["additionalProperties"] = True
        out.append({"name": n, "title": TITLES.get(n, n), "description": tool_desc(n, d), "inputSchema": schema,
                    "annotations": {"title": TITLES.get(n, n), "readOnlyHint": True, "idempotentHint": True, "openWorldHint": n not in LOCAL_ONLY}})
    return out

def call_tool(name, args):
    fn = next((t[4] for t in TOOLS if t[0] == name), None)
    if not fn: raise ToolError("Unknown tool '%s'" % name)
    props = next(t[2] for t in TOOLS if t[0] == name)
    args = {k: v for k, v in (args or {}).items() if v is not None}
    maybe_prune()
    loc_arg = args.pop("location", None)
    if name in NO_LOCATION: loc_arg = None
    if name == "run_recipe": args = flatten_recipe_args(args)
    bad = [k for k in args if k not in props]
    if bad: raise ToolError("Unknown argument(s) %s. Allowed: %s" % (bad, ", ".join(k for k in list(props) + ["location"] if k not in HIDDEN)))
    loc = resolve_location(loc_arg) if loc_arg not in (None, "") else None
    if loc and loc["default"]: loc = None   # the default location needs no override (and keeps local/aliases.json valid)
    prev = rm.current_location()
    try:
        if loc: rm.set_location(loc["id"])
        out = fn(**args)
    finally:
        rm.set_location(prev)
    if isinstance(out, str): out = lift_notice(out)
    return tag_location(out, loc) if loc else out

_wlock = threading.Lock()
def send(msg):
    data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
    with _wlock:
        sys.stdout.buffer.write(data); sys.stdout.buffer.flush()

_loglock = threading.Lock()
LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_PII = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b|\b\d{3}-\d{2}-\d{4}\b|\b\d{9,}\b")

def log_mask(o):
    """Call-log masking, always on (independent of RM_MASK_PII): emails, phone numbers and long digit runs (SSN/account numbers)
    become [masked]; PII-named keys (phone, email, ssn...) are masked entirely."""
    if isinstance(o, dict): return {k: ("[masked]" if pii_key(k) and v not in (None, "", 0) else log_mask(v)) for k, v in o.items()}
    if isinstance(o, list): return [log_mask(v) for v in o]
    if isinstance(o, str): return _LOG_PII.sub("[masked]", o)
    return o

def call_log(tool, args, secs, err, chars, head=""):
    """One JSON line per tools/call in local/mcp_calls.jsonl (gitignored); RM_MCP_LOG=path overrides, RM_MCP_LOG=off disables.
    Args only (never env/credentials; emails/phones/long digit runs masked) plus the first 300 chars of an error (masked too).
    The file rotates at 5 MB to <name>.1.jsonl (one backup). Never raises."""
    path = os.environ.get("RM_MCP_LOG")
    if path is None: path = os.path.join(rm.HERE, "local", "mcp_calls.jsonl")
    if path.lower() in ("", "off", "0", "false"): return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "args": log_mask(mask_args(args)), "secs": round(secs, 3),
               "isError": bool(err), "chars": chars}
        if err: rec["error"] = log_mask(head[:300])
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _loglock:
            try:
                if os.path.getsize(path) > LOG_MAX_BYTES:
                    root, ext = os.path.splitext(path)
                    os.replace(path, root + ".1" + (ext or ".jsonl"))
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as f: f.write(line + chr(10))
    except Exception:
        pass

def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification (e.g. notifications/initialized): no reply
        return
    try:
        if method == "initialize":
            pv = params.get("protocolVersion") or "2025-06-18"
            result = {"protocolVersion": pv if pv in ("2024-11-05", "2025-03-26", "2025-06-18") else "2025-06-18",
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "rentmanager-readonly", "version": VERSION}, "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_list()}
        elif method == "tools/call":
            t0 = time.time()
            try:
                text, err = call_tool(params.get("name"), params.get("arguments") or {}), False
            except ToolError as e:
                text, err = "ERROR: %s" % e, True
            except Exception as e:  # never crash the server; no secrets in tracebacks (rm never logs creds)
                traceback.print_exc(file=sys.stderr)
                text, err = "ERROR (internal): %s: %s" % (type(e).__name__, e), True
            sys.stderr.write("[rm-mcp] %s %.2fs%s\n" % (params.get("name"), time.time() - t0, " ERR" if err else ""))
            call_log(params.get("name"), params.get("arguments") or {}, time.time() - t0, err, len(text), text if err else "")
            result = {"content": [{"type": "text", "text": str(text)}] + list(getattr(text, "extra", ()) or ()), "isError": err}
        elif method in ("resources/list", "prompts/list"):
            result = {method.split("/")[0]: []}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}); return
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    except Exception as e:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})

def serve():
    maybe_prune()   # old result/PDF/download files (see prune_downloads); again once a day while running
    pool = ThreadPoolExecutor(4)
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line: continue
        try:
            msg = json.loads(line.decode("utf-8-sig"))
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}); continue
        for m in (msg if isinstance(msg, list) else [msg]):
            if m.get("method") == "tools/call": pool.submit(handle, m)
            else: handle(m)
    pool.shutdown(wait=True)

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--call":
        sys.stdout.reconfigure(encoding="utf-8")
        try:
            print(call_tool(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}))
        except ToolError as e:
            print("ERROR:", e)
    elif "--charge-types" in sys.argv:
        n = write_charge_types(force="--force" in sys.argv)
        print("charge types: kept existing local/charge_types.json (--force to regenerate)" if n < 0 else "charge types: %d rent/subsidy types -> local/charge_types.json" % n)
    else:
        serve()
