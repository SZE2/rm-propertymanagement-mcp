#!/usr/bin/env python3
"""End-to-end test: start server.py as a subprocess and speak MCP JSON-RPC over stdio.
Measures latency per tool call. Uses live (read-only) data; picks names from the live property list,
so nothing company-specific is hardcoded.

Usage: python3 tests/test_mcp.py
"""
import os, sys, json, time, subprocess, threading, queue
import tempfile
if "--keep-out" not in sys.argv:   # v4 2c: never write test output into the real out/
    os.environ["RM_OUT"] = tempfile.mkdtemp(prefix="rm_test_out_")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Client:
    def __init__(self, env=None):
        self.p = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], cwd=HERE, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.q, self.n = queue.Queue(), 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [None for _ in self.p.stderr], daemon=True).start()  # drain stderr

    def _read(self):
        for line in self.p.stdout:
            self.q.put(line)

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None: msg["params"] = params
        if not notify:
            self.n += 1; msg["id"] = self.n
        self.p.stdin.write((json.dumps(msg) + "\n").encode()); self.p.stdin.flush()
        if notify: return None
        line = self.q.get(timeout=180)
        resp = json.loads(line)   # every stdout line must be valid JSON-RPC
        assert resp.get("id") == msg["id"], resp
        return resp

    def call(self, name, args):
        t0 = time.time()
        r = self.send("tools/call", {"name": name, "arguments": args})
        dt = time.time() - t0
        res = r["result"]
        return dt, res.get("isError"), res["content"][0]["text"]

    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=10)

def main():
    c = Client()
    t0 = time.time()
    init = c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
    print("initialize %.2fs  server=%s  instructions=%d chars" % (time.time() - t0, init["result"]["serverInfo"], len(init["result"].get("instructions", ""))))
    c.send("notifications/initialized", notify=True)
    assert c.send("ping")["result"] == {}
    tools = c.send("tools/list")["result"]["tools"]
    print("tools/list: %d tools: %s" % (len(tools), ", ".join(t["name"] for t in tools)))
    assert c.send("bogus/method").get("error"), "unknown method must error"

    # live names, not hardcoded
    _, _, plist = c.call("resolve", {"term": ""})
    first = plist.splitlines()[1].split(" | ")
    pid, pname = first[0], first[1]
    word = pname.split()[0]
    cases = [
        ("find_recipe", {"query": "who is behind on rent"}, False),
        ("resolve", {"term": word}, False),
        ("run_recipe", {"name": "vacant_units", "params": {"property": pname}}, False),
        ("run_recipe", {"name": "vacancy_by_property", "params": {}}, False),
        ("run_recipe", {"name": "delinquency", "params": {}}, False),            # soft guard: runs portfolio-wide + warns
        ("run_recipe", {"name": "property_snapshot", "params": {"property": pid}}, False),   # bundle, parallel reports
        ("run_recipe", {"name": "delinquency", "params": {"property": pid}}, False),
        ("run_recipe", {"name": "lease_expirations", "params": {"property": pid}}, False),
        ("run_recipe", {"name": "rent_roll", "params": {"property": pid}}, False),
        ("run_recipe", {"name": "profit_and_loss_summary", "params": {"property": pid}}, False),
        ("search_models", {"query": "work orders"}, False),
        ("describe_model", {"model": "Units"}, False),
        ("describe_model", {"model": "Tenants", "section": "filters", "contains": "balance"}, False),
        ("enum_values", {"name": "eLeasePeriod"}, False),
        ("count", {"resource": "Units", "filters": "PropertyID,eq,%s" % pid}, False),
        ("query", {"resource": "Leases", "filters": "PropertyID,eq,%s;MoveOutDate,ge,today" % pid, "fields": "LeaseID,TenantID,UnitID,MoveInDate,EndDate", "max_records": 50}, False),
        ("query", {"resource": "Units", "filters": "Bogus,eq,1"}, True),       # validation error expected
        ("query", {"resource": "Charges", "max_records": 1000000}, True),       # >50 pages without allow_fanout -> refused
        ("list_reports", {"search": "rent roll"}, False),
        ("report_info", {"id": "Delinquency"}, False),
        ("report_pdf", {"report": "67", "properties": pid}, False),
        ("run_report", {"id": "LeaseExpiration", "property": pid, "parameters": "CheckedDateRange,today - today+60"}, False),
        ("query", {"resource": "Units", "fields": "UnitID,PropertyID", "max_records": 100000, "group_by": "PropertyID", "top": 5}, False),
    ]
    rows, slow = [], 0
    for name, args, expect_err in cases:
        dt, err, text = c.call(name, args)
        ok = bool(err) == expect_err and "internal" not in text
        rows.append((name, json.dumps(args)[:70], dt, ok))
        print("%-4s %6.2fs %-15s %s" % ("ok" if ok else "FAIL", dt, name, json.dumps(args)[:80]))
        if not ok: print("     ->", text[:300].replace("\n", " | "))
        if name == "run_recipe" and args["name"] == "delinquency" and not args["params"]:
            assert "portfolio-wide" in text and "Scope: All" in text, "portfolio run must warn and state scope"
        if not err: assert "Scope:" in text or name in ("find_recipe", "resolve", "search_models", "describe_model", "enum_values", "list_reports", "report_info"), (name, text[:200])
        slow += dt > 3
    c.close()
    # fan-out guard: with a budget of 2, the 3rd different Tenants/{id} call must be refused
    c2 = Client(dict(os.environ, RM_FANOUT_BUDGET="2"))
    c2.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    lines = c2.call("query", {"resource": "Tenants", "fields": "TenantID", "max_records": 3})[2].splitlines()
    ids = [l.split("|")[0] for l in lines[lines.index("TenantID") + 1:][:3]]   # rows after the header (a CAPPED line may come first)
    errs = [c2.call("query", {"resource": "Tenants/%s/Balance" % i})[1] for i in ids]
    c2.close()
    fan_ok = errs == [False, False, True]
    print("%-4s fan-out guard (budget 2): errors per call %s" % ("ok" if fan_ok else "FAIL", errs))
    rows.append(("fanout", "", 0.0, fan_ok))
    ts = sorted(r[2] for r in rows)
    print("\n%d calls, %d failed. latency median %.2fs, max %.2fs, >3s: %d" % (len(rows), sum(not r[3] for r in rows), ts[len(ts) // 2], ts[-1], slow))
    return 0 if all(r[3] for r in rows) else 1

if __name__ == "__main__":
    sys.exit(main())
