#!/usr/bin/env python3
"""Guided setup for the read-only Rent Manager MCP server (stdlib only).

Run `PY setup.py --help` for the flag list (PY = py -3 on Windows, python3 on macOS/Linux). --help starts nothing.
Credentials go to .env (gitignored) and are never printed. Use a READ-ONLY Rent Manager user with API access.
"""
import os, sys, json, getpass, subprocess, argparse, glob, shutil, time, shlex

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
PY = "py -3" if os.name == "nt" else "python3"   # how a person (or Claude) should invoke Python in messages


def _rmh():
    """rm.py helpers (.env quoting). Imported lazily: rm.py reads .env at import time."""
    sys.path.insert(0, HERE)
    import rm
    return rm


def ask(prompt, default=""):
    v = input("%s%s: " % (prompt, " [%s]" % default if default else "")).strip()
    return v or default


def read_env():
    env = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); env[k.strip()] = _rmh().parse_env_value(v)
    return env


SSL_HINT = ("  This looks like an SSL certificate problem. On macOS with Python from python.org, run\n"
            "  'Install Certificates.command' (in /Applications/Python 3.x/) once, then re-run setup.\n"
            "  On a company network, a proxy may be re-signing HTTPS; ask your IT team.")


def is_ssl_error(e):
    t = "%s %s" % (type(e).__name__, e)
    return "CERTIFICATE_VERIFY_FAILED" in t or "SSLCertVerificationError" in t or "SSL:" in t


def lock_down(path):
    """chmod 600 on POSIX (no-op on Windows); never raises."""
    if os.name == "posix" and os.path.exists(path):
        try: os.chmod(path, 0o600)
        except OSError: pass


def write_env(company, user, pw, loc, path=None):
    q = _rmh().quote_env_value   # quotes values with leading/trailing quotes or spaces, #, $ or backslash; rm.py reads them back exactly
    path = path or ENV_PATH
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Rent Manager read-only API user. Never commit or paste these.\n")
        f.write("RM_COMPANY=%s\nRM_USERNAME=%s\nRM_PASSWORD=%s\nRM_LOCATION=%s\n" % (q(company), q(user), q(pw), q(loc)))
    lock_down(path)


def env_creds():
    """Credentials from .env, topped up from the process environment (never printed)."""
    e = read_env()
    for k in ("RM_COMPANY", "RM_USERNAME", "RM_PASSWORD", "RM_LOCATION"):
        if not e.get(k) and os.environ.get(k): e[k] = os.environ[k]
    return e


def write_creds():
    old = read_env()
    print("\nStep 1/5  Rent Manager credentials (stored only in .env in this folder).")
    print("  Company code = the first part of https://<company>.api.rentmanager.com")
    company = ask("  Company code", old.get("RM_COMPANY", ""))
    user = ask("  API username", old.get("RM_USERNAME", ""))
    pw = getpass.getpass("  API password (hidden%s): " % (", Enter = keep current" if old.get("RM_PASSWORD") else "")) or old.get("RM_PASSWORD", "")
    loc = ask("  LocationID (Enter = user's default location)", old.get("RM_LOCATION", ""))
    if not (company and user and pw):
        sys.exit("Company code, username and password are required.")
    write_env(company, user, pw, loc)
    tok = os.path.join(HERE, ".token_cache.json")
    if os.path.exists(tok):
        open(tok, "w").write("{}")  # invalidate cached token for the old user (file kept, content cleared)
    print("  saved .env")


def auth_test():
    print("\nStep 2/5  Testing login ...")
    sys.path.insert(0, HERE)
    import importlib, rm
    rm = importlib.reload(rm)   # pick up the .env that was just written
    try:
        rm.get_token(force=True)
        saved = rm.ENV.pop("RM_LOCATION", None)
        s, h, d = rm.get("/Current/Locations")
        if saved is not None: rm.ENV["RM_LOCATION"] = saved
    except Exception as e:
        if is_ssl_error(e): sys.exit("  Login could not connect: %s\n%s" % (e, SSL_HINT))
        if not isinstance(e, rm.RMError): sys.exit("  Login could not connect: %s: %s" % (type(e).__name__, e))
        sys.exit("  Login failed: %s\n  Check .env (company code, username, password) and that the user has API Access enabled.\n  Then re-run: %s setup.py" % (e, PY))
    if s != 200:
        sys.exit("  Login worked but /Current/Locations returned HTTP %s." % s)
    print("  Login OK. (The next line has your business/location names. It is for you: do not paste it into bug reports.)")
    print("  Locations this user can access: %s" % ", ".join("%s (ID %s)" % (l.get("Name"), l.get("LocationID")) for l in d or []))
    if d and len(d) > 1 and not rm.ENV.get("RM_LOCATION"):
        print("  NOTE: several locations. Set RM_LOCATION in .env to pick one (default: the user's default location).")


def run(cmd):
    print("  $ " + " ".join(cmd))
    r = subprocess.run([sys.executable] + cmd, cwd=HERE)
    if r.returncode: sys.exit("  step failed (exit %s). Re-run: %s setup.py (finished parts are kept; the structure pull resumes where it stopped)." % (r.returncode, PY))


def desktop_config_candidates():
    """Possible claude_desktop_config.json paths on this OS (Windows Store wildcard resolved); existing ones first."""
    c = []
    name = "claude_desktop_config.json"
    if os.name == "nt":
        la, ap = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
        if la:
            for p in sorted(glob.glob(os.path.join(la, "Packages", "Claude_*"))):
                c.append(os.path.join(p, "LocalCache", "Roaming", "Claude", name))
        if ap: c.append(os.path.join(ap, "Claude", name))
    elif sys.platform == "darwin":
        c.append(os.path.expanduser("~/Library/Application Support/Claude/" + name))
    else:
        c.append(os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "Claude", name))
    return sorted(c, key=lambda x: not os.path.exists(x))


def server_entry():
    return {"command": sys.executable, "args": [os.path.join(HERE, "server.py")]}


def chatgpt_entry():
    """Local MCP settings shared by ChatGPT desktop and Codex; never includes credentials."""
    return dict(server_entry(), cwd=HERE, startup_timeout_sec=20, tool_timeout_sec=180)


def chatgpt_toml():
    entry = chatgpt_entry()
    lines = ["[mcp_servers.rentmanager]"]
    for key, value in entry.items():
        lines.append("%s = %s" % (key, json.dumps(value, ensure_ascii=False)))
    return "\n".join(lines)


def shell_arg(value):
    # PowerShell double quotes expand $ and backticks; single quotes are literal.
    return "'" + value.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(value)


def print_chatgpt_config():
    """Print registration help only. Does not read .env, connect, or edit any app config."""
    entry = chatgpt_entry()
    print("\nChatGPT desktop / Codex: local MCP registration (nothing was registered automatically).")
    print("  In ChatGPT desktop: Settings > MCP servers > Add server. Choose STDIO.")
    print("  Name: rentmanager")
    print("  Command: %s" % entry["command"])
    print("  Arguments (one item): %s" % json.dumps(entry["args"], ensure_ascii=False))
    print("  Working directory: %s" % entry["cwd"])
    print("  Startup timeout: 20 seconds; tool timeout: 180 seconds (large reports can take longer than the default).")
    print("  Keep your Rent Manager password in .env; do not put credentials into app settings or chat.")
    print("\n  Alternative with the Codex CLI (PowerShell on Windows; a shell on macOS/Linux):")
    print("    codex mcp add rentmanager -- %s %s" % (shell_arg(entry["command"]), shell_arg(entry["args"][0])))
    print("  The CLI shares its MCP configuration with the desktop app. It uses the default tool timeout;")
    print("  for longer reports, set tool_timeout_sec = 180 in the rentmanager table as shown below.")
    print("\n  TOML alternative (~/.codex/config.toml, or .codex/config.toml in a trusted project):")
    print("  Back up an existing file first. Merge this table; keep other settings and servers.")
    print("  If [mcp_servers.rentmanager] already exists, update it instead of adding a duplicate.")
    print(chatgpt_toml())
    print("\n  Save, restart the MCP connection in the app, and start a new local chat/task.")
    print("  Check /mcp, then ask: 'Using rentmanager, find a recipe for vacant units. Do not run it yet.'")
    print("  This local connection does not enable ChatGPT web, mobile, or cloud execution.")
    print("  Full setup and verification: docs/CHATGPT.md")


def print_config():
    py, srv = sys.executable, os.path.join(HERE, "server.py")
    print("\nRegister the MCP server (choose your client; nothing was registered automatically):")
    print("\n  A) Claude Code (run in a terminal):")
    print('    claude mcp add rentmanager -- "%s" "%s"' % (py, srv))
    print("\n  B) Claude Desktop chat and Cowork, the easy way. Run this yourself in your own terminal:")
    print("    %s setup.py --merge-desktop-config" % PY)
    print("     (it finds the config file, asks y/n, backs it up, adds only the rentmanager entry, refuses on invalid JSON)")
    print("\n  C) Claude Desktop by hand: open claude_desktop_config.json (Claude Desktop > Settings > Developer > Edit Config).")
    print("     Config file candidates on this computer (use the first one that exists):")
    for c in desktop_config_candidates():
        print("       %s%s" % (c, "" if os.path.exists(c) else "   (not found)"))
    print('     Add this "rentmanager" entry INSIDE the existing "mcpServers": { ... } object as one more item.')
    print('     Keep your other servers, separate items with commas, and create "mcpServers": { } at the top level if it is missing:')
    print('       "rentmanager": ' + json.dumps(server_entry(), indent=2).replace("\n", "\n       "))
    print("     Result shape: { \"mcpServers\": { \"existing-server\": {...}, \"rentmanager\": {...} } }")
    print("     Then fully quit Claude Desktop (Windows: also the tray icon; macOS: Cmd+Q) and reopen it.")
    print("\n  Then ask Claude e.g.: 'Using rentmanager, which units are vacant at <property>?'")
    print_chatgpt_config()


def merge_desktop_config(path=None, confirm=None):
    """Add/update ONLY the rentmanager entry in claude_desktop_config.json. Backs up first, keeps every other
    server, refuses on invalid JSON. Needs an explicit y from the person running it (confirm() returns the answer)."""
    confirm = confirm or input
    if not path:
        ex = [c for c in desktop_config_candidates() if os.path.exists(c)]
        if not ex:
            print("No claude_desktop_config.json found. Candidates:\n  " + "\n  ".join(desktop_config_candidates()))
            print("Open Claude Desktop > Settings > Developer > Edit Config once (it creates the file), then re-run, or use --config-path FILE.")
            return 1
        path = ex[0]
        if len(ex) > 1: print("Several config files exist; using the first: %s" % path)
    try:
        raw = open(path, encoding="utf-8").read()
        cfg = json.loads(raw) if raw.strip() else {}
        if not isinstance(cfg, dict): raise ValueError("top level is not a JSON object")
        if not isinstance(cfg.get("mcpServers", {}), dict): raise ValueError("mcpServers is not a JSON object")
    except (OSError, ValueError) as e:
        print("Refusing: %s is not usable (%s). Nothing was changed. Fix the file or add the entry by hand (%s setup.py --print-config)." % (path, e, PY))
        return 1
    entry = server_entry()
    cur = cfg.get("mcpServers", {}).get("rentmanager")
    others = [k for k in cfg.get("mcpServers", {}) if k != "rentmanager"]
    if cur == entry:
        print("rentmanager is already registered in %s with these paths. Nothing to do." % path); return 0
    print("Config file: %s" % path)
    print("It already has %d other server(s); they are kept as they are." % len(others))
    print("Will %s the rentmanager entry:\n%s" % ("REPLACE" if cur else "ADD", json.dumps({"rentmanager": entry}, indent=2)))
    if str(confirm("Back up the file and write this change? [y/N] ")).strip().lower() != "y":
        print("Cancelled. Nothing was changed."); return 1
    bak = path + ".bak"
    if os.path.exists(bak): bak = "%s.%s.bak" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, bak)
    cfg.setdefault("mcpServers", {})["rentmanager"] = entry
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
    print("Done. Backup: %s\nNow fully quit Claude Desktop (Windows: also the tray icon; macOS: Cmd+Q) and reopen it." % bak)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="setup.py", description=(
        "Guided setup for the read-only Rent Manager MCP server. With no flags it runs the full setup: credentials -> login test -> "
        "API structure pull (about 10 min the first time; resumable, re-run to continue) -> search index -> local/aliases.json -> "
        "doctor -> registration help. Credentials go only to .env and are never printed. "
        "Python command: py -3 on Windows, python3 on macOS/Linux."))
    ap.add_argument("--non-interactive", action="store_true", help="never prompt; read RM_COMPANY, RM_USERNAME, RM_PASSWORD, RM_LOCATION from .env "
                    "(or the environment). Chosen automatically when there is no terminal (TTY), e.g. when an AI agent runs setup: then .env must already exist")
    ap.add_argument("--skip-creds", action="store_true", help="keep the existing .env instead of asking again")
    ap.add_argument("--aliases", action="store_true", help="only refresh local/aliases.json from live /Properties (keeps your nicknames)")
    ap.add_argument("--index", action="store_true", help="only rebuild spec/index.json (+ live access check)")
    ap.add_argument("--charge-types", action="store_true", help="only write local/charge_types.json (rent/subsidy charge types); kept if it exists")
    ap.add_argument("--force", action="store_true", help="with --charge-types: regenerate even if the file exists")
    output = ap.add_mutually_exclusive_group()
    output.add_argument("--print-config", action="store_true", help="only print registration help for Claude, ChatGPT desktop and Codex; edits nothing")
    output.add_argument("--print-chatgpt-config", action="store_true", help="only print ChatGPT desktop / Codex STDIO settings, CLI command and TOML; edits nothing, needs no credentials")
    ap.add_argument("--merge-desktop-config", action="store_true", help="add the rentmanager entry to claude_desktop_config.json (run it yourself in a terminal; asks y/n, backs up, keeps other servers)")
    ap.add_argument("--config-path", metavar="FILE", help="with --merge-desktop-config: use this config file instead of auto-detecting")
    return ap


def main():
    args = build_parser().parse_args()
    if args.print_chatgpt_config: return print_chatgpt_config()
    if args.print_config: return print_config()
    if args.merge_desktop_config:
        if not sys.stdin.isatty() and not args.config_path:
            sys.exit("Run this yourself in your own terminal (it asks you to confirm with y).")
        sys.exit(merge_desktop_config(args.config_path))
    if args.aliases:
        sys.path.insert(0, HERE); import server
        return print("aliases: %d properties -> local/aliases.json" % server.write_aliases_stub())
    if args.charge_types:
        sys.path.insert(0, HERE); import server
        n = server.write_charge_types(force=args.force)
        return print("charge types: kept existing local/charge_types.json (add --force to regenerate)" if n < 0 else "charge types: %d -> local/charge_types.json" % n)
    if args.index: return run(["spec_index.py", "--verify"])
    print("Rent Manager read-only MCP server setup (Python %s)" % sys.version.split()[0])
    if sys.version_info < (3, 9): sys.exit("Python 3.9+ required.")
    noni = args.non_interactive or not sys.stdin.isatty()
    if noni and not args.non_interactive:
        print("No terminal (TTY) detected: using non-interactive mode. It needs .env (copy .env.example to .env and fill it in yourself).")
    if noni:
        e = env_creds()
        if e.get("RM_COMPANY") and e.get("RM_USERNAME") and e.get("RM_PASSWORD"):
            if e != read_env():   # environment supplied (part of) the values: persist them so the server finds them
                write_env(e.get("RM_COMPANY", ""), e.get("RM_USERNAME", ""), e.get("RM_PASSWORD", ""), e.get("RM_LOCATION", ""))
                print("Wrote .env from the environment (owner-only permissions where supported).")
            else:
                lock_down(ENV_PATH)
        else:
            sys.exit("No usable credentials. Copy .env.example to .env and fill it in yourself (never paste the password into chat), or set RM_COMPANY, RM_USERNAME and RM_PASSWORD in the environment, then re-run.")
    elif not args.skip_creds or not os.path.exists(ENV_PATH):
        try: write_creds()
        except EOFError: sys.exit("No input available (NUL/closed stdin). Copy .env.example to .env and fill it in yourself, then re-run with --non-interactive.")
    auth_test()
    print("\nStep 3/5  Downloading the API structure into spec/ (not business data; ~10 min first time; files already pulled are skipped, so a re-run resumes) ...")
    run(["spec_pull.py"])
    print("\nStep 4/5  Building the search index + checking which resources this user can read ...")
    run(["spec_index.py", "--verify"])
    print("\nStep 5/5  Writing local/aliases.json (property list; add your own nicknames there) ...")
    sys.path.insert(0, HERE); import server
    print("  %d properties" % server.write_aliases_stub())
    try:   # charge-type families (rent / subsidy) for rent summaries; user-editable, kept if it exists
        n = server.write_charge_types()
        print("  charge types: %s" % ("kept existing local/charge_types.json" if n < 0 else "%d rent/subsidy types -> local/charge_types.json (edit if your names differ)" % n))
    except Exception as e:
        print("  charge types skipped (%s); rerun: %s setup.py --charge-types" % (e, PY))
    print("\nChecking everything with doctor.py ...")
    subprocess.run([sys.executable, "doctor.py"], cwd=HERE)
    print_config()


if __name__ == "__main__":
    main()
