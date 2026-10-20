#!/usr/bin/env python3
"""Print a DRAFT GitHub issue body (scrubbed) to stdout. It never files anything and never sends anything.

  PY tools/make_issue.py [--last N] [--no-doctor] [--kind bug|feature] [--note "what happened"]

Reads the last N calls (default 8) from local/mcp_calls.jsonl and a one-line-per-check summary of doctor.py,
then scrubs: emails, phone numbers, digit runs of 3+, dollar amounts, quoted strings, home paths, capitalised
name pairs, the value of RM_USERNAME / RM_COMPANY, property names from local/aliases.json and every term in
local/blocklist.txt. Tool argument VALUES are replaced by placeholders (only names of tools/fields are kept).
PY = py -3 on Windows, python3 on macOS/Linux (never bare python3 on Windows: it may be a Store stub).
ALWAYS read the draft yourself before it is filed anywhere.
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "local", "mcp_calls.jsonl")
KEEP_ARGS = {"name", "resource", "report", "fields", "group_by", "sum", "orderby", "sort", "summary", "kind", "top",
             "columns", "most_recent_first", "first_per", "allow_fanout", "max_records", "embeds"}
SAFE_PHRASES = ("Rent Manager", "Rent Roll", "Claude Desktop", "Claude Code")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(?<![\w.])\+?\d?[\s.-]?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
MONEY = re.compile(r"[-($]*\$\s?\d[\d,]*(?:\.\d+)?[kKM]?\)?")
DIGITS = re.compile(r"\d{3,}")
HOME = re.compile(r"(?:[A-Za-z]:[\\/]+Users[\\/]+|/Users/|/home/)[^\s\"'`<>|]*")
HOST = re.compile(r"[A-Za-z0-9-]+\.api\.rentmanager\.com", re.I)
QUOTED = re.compile(r"\"[^\"\n]{2,}\"|'[^'\n]{2,}'")
NAMEPAIR = re.compile(r"\b[A-Z][a-z]{1,}(?:[ ]+[A-Z][a-z]{1,}){1,3}\b")


def read_env_values():
    vals = {}
    try:
        for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    for k in ("RM_USERNAME", "RM_COMPANY"):
        if os.environ.get(k): vals.setdefault(k, os.environ[k])
    return vals


def collect_terms():
    """Strings that must never appear in a draft: from .env, local/blocklist.txt and local/aliases.json (all untracked)."""
    terms = set()
    env = read_env_values()
    for k in ("RM_USERNAME", "RM_COMPANY"):
        if len(env.get(k, "")) >= 2: terms.add(env[k])
    try:
        for l in open(os.path.join(HERE, "local", "blocklist.txt"), encoding="utf-8"):
            l = l.strip()
            if l and not l.startswith("#"): terms.add(l)
    except OSError:
        pass
    try:
        for p in json.load(open(os.path.join(HERE, "local", "aliases.json"), encoding="utf-8")).get("properties", []):
            for n in [p.get("name"), p.get("short")] + list(p.get("aliases") or []):
                if n and len(n.strip()) >= 3: terms.add(n.strip())
    except (OSError, ValueError):
        pass
    return sorted(terms, key=len, reverse=True)


def scrub_text(s, terms=()):
    s = "" if s is None else str(s)
    for t in terms:
        s = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(t), "<redacted>", s, flags=re.I)
    s = EMAIL.sub("<email>", s)
    s = HOME.sub("<path>", s)
    s = HOST.sub("<company>.api.rentmanager.com", s)
    s = QUOTED.sub("<str>", s)
    s = PHONE.sub("<phone>", s)
    s = MONEY.sub("<amount>", s)
    s = DIGITS.sub("<n>", s)
    keep = {}
    for i, ph in enumerate(SAFE_PHRASES):
        s = s.replace(ph, "\x00%d\x00" % i); keep["\x00%d\x00" % i] = ph
    s = NAMEPAIR.sub(lambda m: "<name>" if not m.group(0).startswith("ERROR") else m.group(0), s)
    for k, v in keep.items(): s = s.replace(k, v)
    return s


def scrub_filters(f, terms):
    """'Field,op,value;Field2,op,value' -> keep Field and op, drop the value."""
    out = []
    for clause in str(f).split(";"):
        parts = clause.split(",", 2)
        out.append("%s,%s,<v>" % (scrub_text(parts[0], terms), scrub_text(parts[1], terms)) if len(parts) == 3 else scrub_text(clause, terms))
    return ";".join(out)


def scrub_value(k, v, terms):
    if k in ("filters", "filter", "parameters"): return scrub_filters(v, terms)
    if k == "params" and isinstance(v, dict): return {pk: "<%s>" % type(pv).__name__ for pk, pv in v.items()}
    if k in KEEP_ARGS and isinstance(v, (str, int, float, bool)): return scrub_text(v, terms) if isinstance(v, str) else v
    return "<%s>" % type(v).__name__


def scrub_call(rec, terms=()):
    args = rec.get("args") if isinstance(rec.get("args"), dict) else {}
    out = {"tool": scrub_text(rec.get("tool", "?"), terms), "secs": rec.get("secs"), "isError": rec.get("isError"),
           "args": {k: scrub_value(k, v, terms) for k, v in args.items()}}
    if rec.get("error"): out["error"] = scrub_text(str(rec["error"])[:300], terms)
    return out


def last_calls(n, path=LOG):
    rows = []
    try:
        for line in open(path, encoding="utf-8"):
            try: rows.append(json.loads(line))
            except ValueError: pass
    except OSError:
        return []
    return rows[-n:]


def doctor_summary(terms):
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "doctor.py")], cwd=HERE, capture_output=True, text=True, timeout=180)
        lines = [l for l in (r.stdout or "").split("\n") if re.match(r"\[(OK |WARN|FAIL)\]", l)]
    except (OSError, subprocess.TimeoutExpired) as e:
        return "(doctor.py could not run: %s)" % scrub_text(type(e).__name__, terms)
    return "\n".join(scrub_text(l, terms) for l in lines) or "(no doctor output)"


def build(n=8, with_doctor=True, kind="bug", note="", log=LOG, terms=None):
    terms = collect_terms() if terms is None else terms
    calls = [scrub_call(c, terms) for c in last_calls(n, log)]
    body = ["<!-- DRAFT. Review every line. Remove anything that identifies a person, tenant, property or company. -->",
            "Beta, unofficial (not affiliated with Rent Manager).", "",
            "## %s" % ("What went wrong" if kind == "bug" else "What I want"), scrub_text(note, terms) or "(describe in general terms)", "",
            "## Environment", "- Python %s on %s" % (sys.version.split()[0], sys.platform), ""]
    if with_doctor: body += ["## doctor.py summary (scrubbed)", "```", doctor_summary(terms), "```", ""]
    body += ["## Last %d tool calls (scrubbed: argument values removed)" % n, "```"]
    body += [json.dumps(c, ensure_ascii=True) for c in calls] or ["(no call log found)"]
    body += ["```"]
    return "\n".join(body)


def main(argv):
    n = 8; kind = "bug"; note = ""; doc = True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--last": n = int(argv[i + 1]); i += 1
        elif a == "--kind": kind = argv[i + 1]; i += 1
        elif a == "--note": note = argv[i + 1]; i += 1
        elif a == "--no-doctor": doc = False
        else: sys.exit(__doc__)
        i += 1
    print(build(n, doc, kind, note))


if __name__ == "__main__":
    main(sys.argv[1:])
