# RM PropertyManagement MCP (unofficial)

> **BETA. Unofficial: not made, endorsed or supported by Rent Manager.** Read-only. Answers can be wrong; check important numbers in Rent Manager. Works in Claude Desktop chat, Cowork (including Cowork started from the mobile app while your computer is on) and Claude Code. Does not work in normal cloud/mobile chats or claude.ai: see [docs/WHERE-IT-WORKS.md](docs/WHERE-IT-WORKS.md). Known limits: [KNOWN-ISSUES.md](KNOWN-ISSUES.md).

**PY** in all commands in this repo means `py -3` on Windows and `python3` on macOS/Linux. On Windows never type bare `python3` (it is often a Microsoft Store stub that does nothing).

## Start here
The repo is public. Pick one:

**Easiest (let Claude do it).** In Claude Desktop (Cowork or Code tab) or a terminal running `claude`, say:

> Clone https://github.com/SZE2/rm-propertymanagement-mcp, open that folder, and follow its CLAUDE.md to set this up for me.

**By hand.**
1. Get the folder onto your computer: `git clone https://github.com/SZE2/rm-propertymanagement-mcp.git` (install git first if needed), or on the repo page click **Code > Download ZIP** and unzip. A ZIP has no history or updates: to update, download it again and copy your `.env` and `local/` files over.
2. The clone creates a folder named `rm-propertymanagement-mcp`. **Open that folder in Claude** (Claude Desktop: Cowork or Code tab, choose it as the working folder; or open a terminal in it and run `claude`).
3. Say: **`Follow CLAUDE.md to set this up for me.`** Claude walks you through the rest. You type your Rent Manager password yourself into a hidden prompt; Claude never needs it.

**Bugs and feature requests: GitHub issues only** (no email). Ask your Claude to file one: it drafts a scrubbed report, shows it to you, and files it only after you say yes.

## What it is
Ask Claude questions about your Rent Manager data ("who is behind on rent at Oak Court?", "vacant units", "NOI last month") and get live answers.
It is a small local MCP server: Python standard library only, nothing to `pip install`, runs on your machine, talks to your own Rent Manager API.

- **Read-only.** Only HTTP GET to Rent Manager (plus the login call). There are no tools that change anything.
- **Live data.** Business data is never cached. Only the API *structure* (spec/) is stored locally.
- **Your data stays on your computer.** Credentials stay in `.env`; results you pull are saved in `out/` on your machine. Both are gitignored.

## You need
- git, and a Rent Manager API subscription (API access may cost extra; ask Rent Manager support, apisupport@rentmanager.com)
- Python 3.9+ (`PY --version`)
- A Rent Manager user with **API Access** enabled, a separate read-only user (never an admin), with View privileges for what you want to ask about (Properties, Units, Customers, Owners, Reports...). See Rent Manager's "API Enabled User Account Setup" guide.
- Your company code: the `<company>` in `https://<company>.api.rentmanager.com`.

## Install (or ask Claude: "Follow CLAUDE.md to set this up for me")
```
PY setup.py          # run it yourself in PowerShell / Terminal in this folder; PY setup.py --help lists the options
```
It asks for company code, username and password (hidden), tests the login, downloads the API structure (~10 min the first time; resumable: re-run to continue), builds the search index, writes `local/aliases.json` (your property list) and runs `doctor.py`.
At the end it prints how to register the server. Nothing is registered automatically:

- **Claude Code:** `claude mcp add rentmanager -- "<python>" "<folder>/server.py"`
- **Claude Desktop / Cowork:** `PY setup.py --merge-desktop-config` (asks y/n, backs up the config, adds only the `rentmanager` entry), then fully quit and reopen the app.

Optional: add nicknames for properties in `local/aliases.json` (`"aliases": ["the tower", "bldg 5"]`).

## Build with Claude (same steps as CLAUDE.md SETUP; details are there)
0. Get the folder and open the session in it (Start here above).
1. Prerequisites: Python 3.9+, git.
2. Rent Manager requirements: API access, a separate View-only API user, company code.
3. The USER runs `PY setup.py` (or fills `.env` and Claude runs `PY setup.py --non-interactive`). Never ask for or paste the password in chat.
4. Health check: `PY doctor.py` (FAIL = stop and fix, WARN = continue but read it).
5. Register: the USER confirms; `PY setup.py --print-config` / `--merge-desktop-config`.
6. Three test questions (using property names from `local/aliases.json`).
7. Tell the user what it can do and where it works.

## Use
Just ask. The server tells Claude its fast path:
1. `find_recipe` -> `run_recipe` (tested shortcuts: delinquency, aged receivables, vacant units, occupancy, lease expirations, rent roll, tenant ledger/open charges, P&L / NOI, trial balance, GL activity, move-ins/outs, charges, payments, owners, and bundles `property_snapshot`, `lender_package`).
2. Built-in reports: `list_reports` -> `report_info` -> `run_report` (one call covers all properties).
3. Anything else in the API: `search_models` -> `describe_model` -> `count` -> `query` (validated, paged in parallel, can group/sum).

`resolve` turns property nicknames into IDs (`resolve('')` lists them) and finds tenants/units/owners/vendors by name.
Every answer states its **Scope** (one property or all). Long results: Claude gives you the file path in `out/` (JSON + CSV).

## PDF reports
`report_pdf(report, properties, parameters)` saves the built-in report as a PDF in `out/`, one file per property (all properties = one portfolio PDF), in parallel. Try: "PDF rent roll for Oak Court, Elm Park and Birch Lane". Read-only; if a report cannot render a PDF the reply says so and `run_report` gives the data instead.

## Maintenance
| Command | When |
|---|---|
| `PY doctor.py` | something seems wrong: checks login, privileges (X-Checked-Privileges), rate limit, index age, recipes |
| `PY spec_pull.py && PY spec_index.py --verify` | after a Rent Manager upgrade or privilege change |
| `PY setup.py --aliases` | properties added/renamed (keeps your nicknames) |
| `PY tests/test_mcp.py` | end-to-end MCP test with latency per tool |
| `PY tests/test_recipes.py [--portfolio]` | live test of every recipe |

Debug a single tool without Claude: `PY server.py --call run_recipe '{"name":"vacant_units","params":{"property":"Oak Court"}}'`

## Files
`server.py` MCP server and tools - `concepts.json` 'where is the data' pointers (rent, deposit, vacancy...) + `local/charge_types.json` rent/subsidy charge types (written by setup/doctor, editable) - `rm.py` GET-only API client + throttle - `spec_pull.py` / `spec_index.py` API structure + search index - `recipes/*.json` shortcuts - `setup.py`, `doctor.py` - `tests/`, `tools/` (issue drafting, public-tree builder).
Recipe format: `name, description, keywords, heavy, params{name: {type: date|int|number|bool|str|property|tenant, default, description}}, request: {report, parameters} | {query: {resource, filters[], fields, embeds, orderby}} | {bundle: [{recipe, params}]}, post: {where, rename, group_by, sum, sort, top, columns, running}`. Placeholders `{param}`; a filter/parameter whose placeholder is empty is dropped.

## Limits
500 API requests/minute (the server throttles at 7/s). Reports across a large portfolio can take 20-40 s; give a property for speed. Report IDs in recipes are standard Rent Manager IDs; `doctor.py` flags any recipe that does not match your install.

License: MIT (see LICENSE). Rent Manager is a trademark of its owner; this project is not affiliated with it.
