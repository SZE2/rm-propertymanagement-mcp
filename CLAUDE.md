# RM PropertyManagement MCP (unofficial) - setup guide for Claude

You are reading this because the user asked you to set this project up (for example: "Follow CLAUDE.md to set this up for me"). Follow SETUP below, step by step, and talk in plain words. The user may not be technical.

**Status: BETA. UNOFFICIAL.** This is a community project. It is not made, endorsed or supported by Rent Manager. It is read-only (it can only look at data, never change it). Answers can be wrong; tell the user to check important numbers in Rent Manager. Limits and where it works: `docs/WHERE-IT-WORKS.md`.

## Rules for you (always)
1. **Never ask for, accept or repeat the Rent Manager password in chat.** If the user pastes one, tell them to change it in Rent Manager, and do not use it. The user types it into the hidden prompt of `setup.py` in their own terminal (or hand-edits `.env`). You only confirm that login worked.
2. Never print or echo the contents of `.env` or `.token_cache.json`.
3. Read-only: do not add tools or code that change Rent Manager.
4. Ask the user before you edit any file outside this folder (Claude Desktop config) and before you file a GitHub issue.
5. No company names, tenant names, property names, emails, phone numbers or dollar amounts in anything you publish (issues, commits).

## SETUP

**PY = `py -3` on Windows, `python3` on macOS/Linux.** Use PY in every command below. On Windows never use bare `python3` (it is often a Microsoft Store stub that does nothing). `PY setup.py --help` lists every option and starts nothing.

### Step 0. Get the code and open the session in it
The repo is public (https://github.com/SZE2/rm-propertymanagement-mcp). If this CLAUDE.md is already in your working folder, skip this step. Otherwise:
1. Get the folder: `git clone https://github.com/SZE2/rm-propertymanagement-mcp.git` (install git first if missing: macOS `brew install git`, Windows `winget install --id Git.Git`), OR without git, GitHub web **Code > Download ZIP** and unzip. A ZIP has no git history; updating means downloading a new ZIP (keep `.env` and `local/`).
2. The clone lands in a folder named `rm-propertymanagement-mcp` inside the folder the terminal was in. The Claude session must be opened IN that folder: tell the user to reopen it there (Claude Desktop Cowork or Code tab with that working folder, or `claude` in a terminal inside it).

### Step 1. Prerequisites check
Run these and report what is missing:
- `PY --version`. Need **Python 3.9 or newer**.
- `git --version` (not needed for a ZIP install).
Offer to install what is missing, and ask first:
- Windows: `winget install --id Python.Python.3.12` and `winget install --id Git.Git`, then open a NEW terminal.
- macOS: install Homebrew from https://brew.sh if needed, then `brew install python git`.
Nothing needs `pip install`; the project uses only the Python standard library.

### Step 2. Rent Manager requirements (ask the user)
- **API access must be enabled for their Rent Manager company.** It may be a paid extra. If it is not on, they contact Rent Manager support (apisupport@rentmanager.com) and ask for API access for their company.
- Create a **separate, API-enabled Rent Manager user with View privileges only** (Properties, Units, Customers/Tenants, Leases, Charges, Payments, Owners, Reports, and general ledger accounts for accounting reads). It must NOT be an admin or their own login. Rent Manager's guide: "API Enabled User Account Setup". `doctor.py` checks all of these reads (Step 4).
- Note the **company code**: the `<company>` in `https://<company>.api.rentmanager.com`.
- Note the **location** if the company has several (Rent Manager "locations" are separate databases). Blank means the user's default location.
- The user keeps the username and password to themselves.

### Step 3. Credentials and install (the user types the password)
Tell the user to open their own terminal (PowerShell on Windows, Terminal on macOS; not Git Bash, whose hidden prompt can fail) in this folder and run `PY setup.py`. It asks for company code, username, password (hidden) and location, tests the login, downloads the API structure, builds the index, writes `local/aliases.json`, runs `doctor.py` and prints how to register the server.
- **It takes about 10 minutes the first time** (the API structure pull). Agent shell commands time out after about 2 minutes, so never run it in your own foreground shell. Either the user runs it in their own terminal (best) or, after `.env` exists, you run `PY setup.py --non-interactive` as a background command and poll its output until it ends. It is resumable: files already pulled are skipped, so if it stops or times out, run it again and it continues.
- Without a terminal (TTY), `setup.py` automatically switches to non-interactive mode, which needs `.env` to exist. If the hidden prompt does not work: tell them to copy `.env.example` to `.env`, fill in the four lines in a text editor (values with quotes, spaces, `#` or `$` may be wrapped in double quotes), save, then you run `PY setup.py --non-interactive`.
- **Expected output:** `Login OK`, then a line listing the Rent Manager location names this user can access (these are business names: do not repeat them into issues or commits), then step lines 3/5 to 5/5 (structure pull, index, a property count and charge-type count), then the `doctor.py` report and the registration help. Confirm to the user only "login OK" or the error message (error messages do not contain the password). Do not ask what they typed.

### Step 4. Health check
Run `PY doctor.py` and explain each line in plain words. Line types:
- **FAIL = stop and fix.** Do not continue until none remain, with one exception below.
  - login failed: wrong code/username/password, or the user lacks API access. The user re-runs `PY setup.py`.
  - `.env has ...` missing values: run `PY setup.py`.
  - `spec/index.json missing`: run `PY spec_pull.py` then `PY spec_index.py --verify` (long; background, see Step 3).
  - macOS `CERTIFICATE_VERIFY_FAILED`: Python from python.org needs a one-time step: run `Install Certificates.command` from `/Applications/Python 3.x/` (Finder), then retry.
  - `recipes: ... invalid against this API spec`: note the recipe names for a bug report. The other tools still work; continue only if the user agrees.
- **WARN = continue**, but read it. A WARN on a privilege / HTTP 204 line for Properties, Units, Tenants, Leases, Charges, Payments, Owners, Reports or the accounting read (`/GLAccounts`) means **that data will be missing from answers**: tell the user to fix the API user's View privileges or property access in Rent Manager before relying on answers, then run `PY spec_index.py --verify` and `PY doctor.py` again.
- OK lines need no action.

### Step 5. Register the MCP (ask first)
`PY setup.py --print-config` prints, for this machine, the Claude Code command, the exact `"rentmanager": {...}` entry, where it goes, and the config file paths to check. Show it to the user and ask permission before changing anything.
**Claude Desktop (chat + Cowork):**
- Easiest: the user runs `PY setup.py --merge-desktop-config` in their own terminal. It finds the config file (including the Windows Store `Claude_*` folder), shows the change, asks y/n, backs the file up, adds only the `rentmanager` entry and keeps every other server. It refuses on invalid JSON. (It must be run by the user: it needs a keyboard y.)
- By hand: put the printed `"rentmanager": {...}` entry INSIDE the existing `"mcpServers": { ... }` object as one more item (commas between items; create `"mcpServers": { }` at the top level if missing). Config file candidates: Windows Store app `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`, Windows normal install `%APPDATA%\Claude\`, macOS `~/Library/Application Support/Claude/`, or Claude Desktop > Settings > Developer > Edit Config. Back it up first (`claude_desktop_config.json.bak`). **Merge, never overwrite:** keep every other server, keep valid JSON.
- Then the user must **fully quit Claude Desktop** (Windows: also the tray icon; macOS: Cmd+Q) and reopen it.
**Claude Code alternative:** `claude mcp add rentmanager -- "<python>" "<folder>/server.py"` (use the full paths that `setup.py --print-config` prints).

### Step 6. Three test questions
Read `local/aliases.json` (the property list; it holds business names, so do not put them in issues) and offer the user a few valid property names to choose from. Ask these with the MCP on (new chat), using the property the user chooses:
1. "Which units are vacant at <property>?" (expect a table with a Scope line)
2. "Who is behind on rent at <property>? Show balance and days late." (delinquency)
3. "Make a PDF rent roll for <property>." (expect a file path in `out/`)
If an answer is empty (HTTP 204), the API user lacks a privilege or access to that property: go back to Step 4 and fix it in Rent Manager. If a tool does not appear, the app was not fully restarted or the config is invalid JSON.

### Step 7. Tell the user
- What it can do: financials (P&L, trial balance, GL), vacancy, delinquency, tenant history, rent roll, PDFs, lease and other file attachments (`find_attachments`, `download_file`), several locations (`locations`, `location=`), and free questions on the whole API.
- Where it works: Claude Desktop chat, Cowork (including Cowork started from the mobile app while the computer is on), and Claude Code. It does not work in normal cloud/mobile chats or claude.ai. Details: `docs/WHERE-IT-WORKS.md`.
- How to report a problem (below).

## Privacy
- Whatever the MCP returns is sent to the AI provider as part of the chat, including tenant and owner names and amounts. The user should follow their company's data policy. Optional masking of phone/email/SSN fields: set `RM_MASK_PII=1` (names are not masked).
- Credentials stay in `.env` on this computer. Results are saved in `out/` on this computer (not cleaned automatically).
- The call log `local/mcp_calls.jsonl` stays on this computer and holds tool arguments (they can contain names). It is never sent anywhere unless the user chooses to share an excerpt. Delete it any time. `RM_MCP_LOG=off` disables it.
- Not affiliated with Rent Manager. Rent Manager is a trademark of its owner.

## Bugs and feature requests (GitHub issues only)
Offer this whenever something fails, is wrong, or the user wants something new. There is no email support.
1. Collect facts: what the user asked, what happened, `doctor.py` output. For a draft from the call log run `PY tools/make_issue.py` (prints a scrubbed draft to the screen; it files nothing).
2. **Show the full draft to the user.** Ask: "Should I file this on GitHub?" Wait for a clear yes. Let them edit it.
3. Only after a yes: `gh issue create --repo SZE2/rm-propertymanagement-mcp --title "<title>" --body-file <file>` (labels: bug or enhancement). If `gh` is not available, give the user a prefilled link: `https://github.com/SZE2/rm-propertymanagement-mcp/issues/new?title=<url-encoded>&body=<url-encoded, short>`.
4. **Never include** credentials, company code, tenant or owner names, emails, phone numbers, dollar amounts, or property or location names. Describe by tool name, recipe name, error text and field names. Re-read the draft for these before showing it.

## Using it later (normal questions)
Follow the MCP's own instructions: prefer `find_recipe` -> `run_recipe`, then built-in reports (`list_reports` -> `run_report`), then a validated free query (`search_models` -> `describe_model` -> `query`). State the Scope line of each answer. Quote numbers exactly as returned.

## Developing this repo
Developer notes (offline tests, hygiene check, public-tree builder): `docs/DEVELOPING.md`.
Do not change the read-only guarantee: GET only (plus the single login POST).
