#!/usr/bin/env python3
"""Read-only Rent Manager API client. GET + token auth only; no write methods exist here.

Usage:
  python3 rm.py locations
  python3 rm.py get /Properties [--loc 1] [--filters "IsActive,eq,true"] [--fields A,B] [--embeds X,Y] [--all] [--max N] [--raw]
  python3 rm.py report <ReportID> "AsOfDate,2026-10-01;PropertyIDs,(16,37)" [--loc 1]
  python3 rm.py reports                  # list available reports
  python3 rm.py meta <ReportID>          # report parameter metadata
Credentials: .env in this folder (RM_COMPANY, RM_USERNAME, RM_PASSWORD, optional RM_LOCATION).
"""
import json, socket, os, sys, time, threading, urllib.request, urllib.error, urllib.parse, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, ".token_cache.json")

def parse_env_value(raw):
    """Value part of a .env line. "double quoted" values use backslash escapes for backslash and quote (what setup.py writes); 'single quoted'
    values are literal; anything else is taken as-is (trimmed)."""
    v = raw.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        out, i, body = [], 0, v[1:-1]
        while i < len(body):
            if body[i] == "\\" and i + 1 < len(body) and body[i + 1] in '\\"':
                out.append(body[i + 1]); i += 2
            else:
                out.append(body[i]); i += 1
        return "".join(out)
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1]
    return v

def quote_env_value(v):
    """Inverse of parse_env_value: quote only when needed (quotes, spaces at the ends, #, $, backslash, newline)."""
    v = str(v).replace("\r", " ").replace("\n", " ")
    if v and not (v != v.strip() or v[0] in "\"'" or v[-1] in "\"'" or any(c in v for c in "#$\\")):
        return v
    return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')

def load_env():
    """OS environment first, then .env on top: values in .env WIN over stale RM_* variables in the OS environment."""
    env = dict(os.environ)
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = parse_env_value(v)
    return env

ENV = load_env()
BASE = "https://%s.api.rentmanager.com" % ENV.get("RM_COMPANY", "")

class RMError(Exception):
    """Raised for auth/config problems (never contains credentials)."""

# Shared throttle: stay under the 500 req/rolling-minute limit (default 7 req/s = 420/min).
RATE = float(ENV.get("RM_RATE", "7"))
_tlock, _tlast = threading.Lock(), [0.0]
_toklock = threading.Lock()
LAST_RATE = {}  # last seen x-ratelimit-* headers

def _throttle():
    with _tlock:
        now = time.time(); wait = _tlast[0] + 1.0 / RATE - now
        _tlast[0] = max(now, _tlast[0] + 1.0 / RATE)
    if wait > 0: time.sleep(wait)
    rem = LAST_RATE.get("x-ratelimit-remaining")
    if rem is not None and str(rem).isdigit() and int(rem) < 15:
        reset = LAST_RATE.get("x-ratelimit-reset", "")
        time.sleep(min(30, max(1, int(reset) - int(time.time()))) if str(reset).isdigit() else 5)

_LOC = threading.local()   # per-call location override (set by the MCP server for a call's `location` argument)

def set_location(loc):
    """Location (LocationID) used by get/get_bytes in THIS thread when no explicit loc is passed. None clears it."""
    _LOC.v = str(loc) if loc not in (None, "") else None

def current_location():
    return getattr(_LOC, "v", None)

def bind(fn):
    """Wrap fn so a worker thread uses the location of the thread that created the wrapper."""
    loc = current_location()
    def run(*a, **k):
        old = current_location(); set_location(loc)
        try: return fn(*a, **k)
        finally: set_location(old)
    return run

def _effective_loc(loc):
    """None -> thread-local, else RM_LOCATION; '' -> no location header (default location of the user)."""
    if loc is None:
        loc = current_location() or ENV.get("RM_LOCATION")
    return loc

_TL = threading.local()   # per-thread (headers_s, body_s, bytes) of the last request, for the RM_TIMING log
TIMING = ENV.get("RM_TIMING", "") not in ("", "0")

DEFAULT_TIMEOUT = float(ENV.get("RM_TIMEOUT", "90"))

def _req(method, path, headers=None, body=None, timeout=None, binary=False):
    # read-only guard: GET, plus the single login POST (the API requires POST to obtain a token)
    if not (method == "GET" or (method == "POST" and path == "/Authentication/AuthorizeUser")):
        raise RMError("read-only: method %s %s is not allowed" % (method, str(path).split("?")[0]))
    timeout = timeout or DEFAULT_TIMEOUT
    if not ENV.get("RM_COMPANY"):
        raise RMError("RM_COMPANY missing in .env (run setup.py)")
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            t1 = time.time()   # response headers received
            raw = resp.read()
            _TL.t = (t1 - t0, time.time() - t1, len(raw))
            return resp.status, dict(resp.headers), (raw if binary else raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        t1 = time.time()
        raw = e.read()
        _TL.t = (t1 - t0, time.time() - t1, len(raw))
        return e.code, dict(e.headers), (raw if binary else raw.decode("utf-8", "replace"))

def get_token(force=False):
    with _toklock:
        if not force and os.path.exists(TOKEN_FILE):
            try:
                t = json.load(open(TOKEN_FILE))
                if time.time() - t["ts"] < 14 * 60:  # 15-min inactivity expiry
                    return t["token"]
            except (ValueError, KeyError):
                pass
        u, p = ENV.get("RM_USERNAME"), ENV.get("RM_PASSWORD")
        if not u or not p:
            raise RMError("Missing RM_USERNAME / RM_PASSWORD in .env (run setup.py)")
        # The only non-GET call: authentication (POST is required by the API to obtain a token).
        s, _, b = _req("POST", "/Authentication/AuthorizeUser", body={"UserName": u, "Password": p})
        if s != 200:
            try: msg = json.loads(b).get("DeveloperMessage") or json.loads(b).get("Message")
            except Exception: msg = b[:200]
            raise RMError("Auth failed (HTTP %s): %s" % (s, msg))
        tok = json.loads(b) if b.startswith('"') else b.strip('"')
        if isinstance(tok, dict):
            tok = tok.get("Token") or tok.get("token")
        _write_token({"token": tok, "ts": time.time()})
        return tok

def private_file(path):
    """chmod 600 on POSIX (no-op on Windows); never raises."""
    if os.name == "posix":
        try: os.chmod(path, 0o600)
        except OSError: pass

def _write_token(t):
    """Atomic write (temp file + replace) so a concurrent reader never sees a half-written cache."""
    tmp = "%s.%d.tmp" % (TOKEN_FILE, os.getpid())
    try:
        with open(tmp, "w") as f: json.dump(t, f)
        private_file(tmp)
        os.replace(tmp, TOKEN_FILE)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass

TOUCH_EVERY = 60  # seconds: rewrite the cache timestamp at most this often

def _touch_token():
    # tokens expire after 15 min of INACTIVITY; each successful call extends it (cache refreshed at most once a minute)
    try:
        with _toklock:
            t = json.load(open(TOKEN_FILE))
            if time.time() - t["ts"] >= TOUCH_EVERY:
                t["ts"] = time.time(); _write_token(t)
    except Exception:
        pass

RETRY_STATUS = (502, 503, 504)
RETRY_DELAY = 1.0
class _Transient(Exception): pass

def get(path, params=None, loc=None, retry=True, extra_headers=None, _429=0, timeout=None):
    """GET only. Returns (status, headers, parsed_json_or_text)."""
    loc = _effective_loc(loc)
    hdr = {"x-rm12api-apitoken": get_token()}
    if loc:
        hdr["x-rm12api-locationid"] = str(loc)
    hdr.update(extra_headers or {})
    params = {k: v for k, v in (params or {}).items() if v not in ("", None)}  # empty filters= is rejected by the API
    q = ("?" + urllib.parse.urlencode(params, safe=",;()")) if params else ""
    _throttle()
    try:
        s, h, b = _req("GET", path + q, hdr, timeout=timeout)
        if s in RETRY_STATUS: raise _Transient()
    except (_Transient, urllib.error.URLError, ConnectionError) as e:
        if isinstance(e, urllib.error.URLError) and isinstance(e.reason, socket.timeout): raise
        time.sleep(RETRY_DELAY); _throttle()   # one retry of a transient failure; a GET is always safe to repeat
        s, h, b = _req("GET", path + q, hdr, timeout=timeout)
    h = {k.lower(): v for k, v in h.items()}
    LAST_RATE.update({k: v for k, v in h.items() if k.startswith("x-ratelimit")})
    if s == 401 and retry:
        get_token(force=True)
        return get(path, params, loc, False, extra_headers, timeout=timeout)
    if s == 429 and _429 < 5:
        reset = h.get("x-ratelimit-reset", "")
        time.sleep(min(60, max(1, int(reset) - int(time.time()))) if str(reset).isdigit() else 5)
        return get(path, params, loc, retry, extra_headers, _429 + 1, timeout=timeout)
    if s < 400:
        _touch_token()
    b = b.lstrip("\ufeff")  # report downloads start with a UTF-8 BOM
    tp = time.time()
    try:
        out = s, h, json.loads(b) if b else None
    except ValueError:
        out = s, h, b
    if TIMING:   # log only (stderr): where does the time go? never prints headers/tokens
        th, tb, n = getattr(_TL, "t", (0, 0, 0))
        sys.stderr.write("[rm.timing] GET %s status=%s headers=%.2fs body=%.2fs bytes=%d parse=%.2fs\n" % (path.split("?")[0], s, th, tb, n, time.time() - tp))
    return out

def get_bytes(path, params=None, loc=None, retry=True, extra_headers=None, _429=0, timeout=None, url_path=None):
    """GET only, binary-safe (PDF streams). Same throttle/token/retry rules as get(). Returns (status, headers, bytes)."""
    loc = _effective_loc(loc)
    hdr = {"x-rm12api-apitoken": get_token()}
    if loc:
        hdr["x-rm12api-locationid"] = str(loc)
    hdr.update(extra_headers or {})
    params = {k: v for k, v in (params or {}).items() if v not in ("", None)}  # empty filters= is rejected by the API
    q = ("?" + urllib.parse.urlencode(params, safe=",;()")) if params else ""
    _throttle()
    try:
        s, h, b = _req("GET", path + q, hdr, timeout=timeout, binary=True)
        if s in RETRY_STATUS: raise _Transient()
    except (_Transient, urllib.error.URLError, ConnectionError) as e:
        if isinstance(e, urllib.error.URLError) and isinstance(e.reason, socket.timeout): raise
        time.sleep(RETRY_DELAY); _throttle()
        s, h, b = _req("GET", path + q, hdr, timeout=timeout, binary=True)
    h = {k.lower(): v for k, v in h.items()}
    LAST_RATE.update({k: v for k, v in h.items() if k.startswith("x-ratelimit")})
    if s == 401 and retry:
        get_token(force=True)
        return get_bytes(path, params, loc, False, extra_headers, timeout=timeout)
    if s == 429 and _429 < 5:
        reset = h.get("x-ratelimit-reset", "")
        time.sleep(min(60, max(1, int(reset) - int(time.time()))) if str(reset).isdigit() else 5)
        return get_bytes(path, params, loc, retry, extra_headers, _429 + 1, timeout=timeout)
    if s < 400:
        _touch_token()
    return s, h, b

# ----------------------------------------------------------------------------- file downloads (signed rm12filereader links)
MAX_FILE_BYTES = 2 * 1024 ** 3        # hard limit for a streamed download (2 GB)
MAX_MEM_BYTES = 50 * 1024 ** 2        # limit for an in-memory download (mode=text)

def file_host_ok(host):
    h = (host or "").lower().rstrip(".")
    return h == "rentmanager.com" or h.endswith(".rentmanager.com")

def check_file_url(url):
    """Only absolute https URLs on rentmanager.com / *.rentmanager.com, default port, no credentials. Returns the parsed URL."""
    try:
        u = urllib.parse.urlparse(str(url).strip())
        port = u.port
    except ValueError:
        raise RMError("not a valid file URL")
    if u.scheme != "https":
        raise RMError("file URL must be https")
    if not file_host_ok(u.hostname) or u.username or u.password or port not in (None, 443):
        raise RMError("file URL host is not a rentmanager.com host; refusing to fetch it")
    return u

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_file_url(urllib.parse.urljoin(req.full_url, newurl))   # raises RMError for another host / http
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch_file(url, dest=None, max_bytes=None, timeout=None, _open=None):
    """GET one absolute rentmanager.com https URL WITHOUT the API token (the link is a signed capability). GET only.
    dest=None: read into memory (limit MAX_MEM_BYTES) and return the bytes. dest=path: stream to '<dest>.part' in chunks, then rename to dest
    (never holds the file in memory; limit MAX_FILE_BYTES; over the limit the partial file is removed and RMError raised).
    Returns (status, headers, data_or_None, size, sha256_hex). Non-200: nothing is written, data = first bytes of the reply."""
    import hashlib
    check_file_url(url)
    limit = max_bytes or (MAX_MEM_BYTES if dest is None else MAX_FILE_BYTES)
    _throttle()
    opener = urllib.request.build_opener(_SafeRedirect)
    req = urllib.request.Request(str(url).strip(), method="GET", headers={"User-Agent": "rm-mcp"})
    try:
        resp = (_open or opener.open)(req, timeout=timeout or DEFAULT_TIMEOUT)
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(2000), 0, None
    with resp:
        status = getattr(resp, "status", 200)
        headers = {k.lower(): v for k, v in dict(resp.headers).items()}
        try:
            if int(headers.get("content-length", 0)) > limit:
                raise RMError("file is larger than the %d MB limit" % (limit // 1024 ** 2))
        except ValueError:
            pass
        h, n, chunks = hashlib.sha256(), 0, []
        part = (dest + ".part") if dest else None
        f = open(part, "wb") if part else None
        try:
            while True:
                b = resp.read(65536)
                if not b: break
                n += len(b)
                if n > limit:
                    raise RMError("file is larger than the %d MB limit; download stopped" % (limit // 1024 ** 2))
                h.update(b)
                if f: f.write(b)
                else: chunks.append(b)
            if f:
                f.close(); f = None
                os.replace(part, dest)
        except BaseException:
            if f:
                f.close()
            if part and os.path.exists(part):
                try: os.remove(part)   # our own partial file
                except OSError: pass
            raise
    return status, headers, (None if dest else b"".join(chunks)), n, h.hexdigest()

def get_all(path, params=None, loc=None, page_size=1000, max_records=None):
    params = dict(params or {})
    out, page = [], 1
    while True:
        params.update(pageSize=page_size, pageNumber=page)
        s, h, d = get(path, params, loc)
        if s != 200 or not isinstance(d, list):
            if s not in (200, 204):
                print("HTTP %s: %s" % (s, str(d)[:500]), file=sys.stderr)
            break
        out += d
        if len(d) < page_size or (max_records and len(out) >= max_records):
            break
        page += 1
    return out[:max_records] if max_records else out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("params", nargs="?")
    ap.add_argument("--loc"); ap.add_argument("--filters"); ap.add_argument("--fields")
    ap.add_argument("--embeds"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--max", type=int); ap.add_argument("--orderby")
    a = ap.parse_args()
    try:
        res = run_cmd(a)
    except RMError as e:
        sys.exit(str(e))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(res, indent=1) if not isinstance(res, str) else res)

def run_cmd(a):
    if a.cmd == "locations":
        # no location header on the initial lookup, per RM docs
        saved = ENV.pop("RM_LOCATION", None)
        res = get("/Current/Locations")[2]
        if saved: ENV["RM_LOCATION"] = saved
    elif a.cmd == "reports":
        res = get("/Reports", {"fields": "ReportID,Name"}, a.loc)[2]
    elif a.cmd == "meta":
        res = get("/Reports/%s" % a.arg, {"embeds": "ReportParameters.ReportParameterValueSource"}, a.loc)[2]
    elif a.cmd == "report":
        res = get("/Reports/%s/RunReport" % a.arg, {"parameters": a.params or ""}, a.loc)[2]
    elif a.cmd == "get":
        p = {k: v for k, v in dict(filters=a.filters, fields=a.fields, embeds=a.embeds, orderby=a.orderby).items() if v}
        res = get_all(a.arg, p, a.loc, max_records=a.max) if a.all else get(a.arg, p, a.loc)[2]
    else:
        sys.exit(__doc__)
    return res

if __name__ == "__main__":
    main()
