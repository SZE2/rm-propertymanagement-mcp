# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/) in its `0.MINOR.PATCH` form (see [docs/MAINTAINING.md](docs/MAINTAINING.md)). While the version starts with 0, anything may change between releases.


On 2026-10-19 the public git history was reset to start at 0.10.0-beta. The entries below for 0.9.0-beta and earlier are kept as a record of what changed; their individual tags and releases no longer exist.

## [Unreleased]

- Fixed: `query` and `count` reject literal `null` comparisons on schema-declared DateTime filters before any API request, with real-date and local missing-date guidance. Non-date and unknown-type filters are unchanged.

## [0.10.0-beta] - 2026-10-18
- Added: `docs/WEB-CHAT.md` explains why claude.ai web chat and the phone app's normal chat are not supported yet and what it would take (hosting or Anthropic's Enterprise-only MCP tunnels, HTTP transport, sign-in).
- Changed: when rows are missing, the reply now STARTS with one `MORE: K of N rows shown; the rest is NOT in this reply. ...` line (second, after any `CAPPED`/`PARTIAL` line; above `Answer:` and the table) with `read_result` and the local `File:` as before (`RM_DELIVERY` still applies); the end of the reply keeps only a one-line `See MORE above` pointer. A result over `RM_BIG_ROWS` rows (default 1000) shows only `RM_SAMPLE_ROWS` rows (default 10) unless `show` is given, and the notice says `LARGE RESULT`.

## [0.9.0-beta] - 2026-10-18
- Fixed: long results were cut at 9,000 characters in the middle of a row (`...[output clipped; full data in the saved file]`), which also cut off the saved-file path; clients without access to this computer's disk could not get the rest at all. Raising `show` or `max_records` did not help.
- Added: `read_result(result_id, offset, limit, columns, max_chars)` pages the full table of an earlier reply as CSV through MCP (default 20,000 characters per page, up to 60,000). Each page ends with `END OF PAGE rows a-b of N` and the next offset, or `All N rows delivered.`
- Changed: inline tables are fitted to the reply size in whole rows. When rows are left out, a `MORE: n of N rows shown` line offers `read_result` (any client) and the local `File:` (agents whose file tools run on this computer); the model picks. `RM_DELIVERY=auto|mcp|file` (default `auto`) limits the offer.
- Changed: data replies no longer repeat the 'show this file to the user' reminder (PDFs and downloads still do). When the table shown differs from the saved rows (`columns`, `group_by`, `sort`), it is also saved as `<name>_view_<stamp>.csv` so `read_result` pages exactly that table.

## [0.8.0-beta] - 2026-10-18
- Added: optional ChatGPT web Secure MCP Tunnel setup and troubleshooting, including runtime key handling, workspace association, restart requirements and verification boundaries. A web recipe lookup passed through the private tunnel; live web data queries and file delivery remain separate checks.
- Added: ChatGPT desktop / Codex STDIO setup guide and `setup.py --print-chatgpt-config` (exact UI settings, safely quoted CLI command and escaped TOML; no credentials or app settings are changed).
- Changed: initialization guidance puts scope, read-only use and accurate answers in its first 512 characters; local file guidance works across clients without promising an upload.
- Added: offline subprocess compatibility tests for desktop-style startup, tool discovery, recipe lookup, notifications and errors. The installed OpenAI backend passed discovery and scoped live vacancy, delinquency and PDF checks from the saved configuration after approved activation. A live desktop conversation also discovered all 15 tools and successfully called `find_recipe`. Broader conversational accuracy evaluations remain release work.
- Fixed: the public-file hygiene check recognizes linked Git worktrees, whose `.git` is a file. If Git refuses access to a checkout, the check stops instead of falling back to scanning private development files.

## [0.7.1-beta] - 2026-10-14
- Added: row cap. A raw-row pull returns at most `RM_MAX_ROWS` rows (default 5000); an explicit `max_records` raises it up to `RM_MAX_ROWS_HARD` (default 20000). Whenever rows are missing the answer starts with a `CAPPED: showing the newest N of M rows ...` line (newest rows are kept where the API can sort by date) that says how to get the rest.
- Added: Rent Manager `OutOfMemoryException` handling. The request is retried with smaller pages (1000, 500, 250, 100, 50); rows already read come back flagged `PARTIAL`; otherwise a clear error with narrowing advice. `run_report` gets the same message.
- Added: `report_suggestions` in `concepts.json`: which built-in report covers a ledger, charges, payments, history notes, recurring charges, leases or the general ledger. Shown by `find_recipe` and `search_models` and inside the CAPPED and out-of-memory text.
- Changed: `tenant_ledger` reads the newest 1000 rows per tenant by default, accepts `since`, `until`, `max_rows` and several tenants (`tenant` as a list), and its running balance is the ledger's own balance even when older rows are not listed.
- Changed: the `most_recent_first` notice no longer says the newest rows are missing: the newest rows are now read first.

## [0.7.0-beta] - 2026-10-14
- Added: `find_attachments` finds files attached in Rent Manager (tenant, unit, owner, vendor and property history notes, service issues, leases and lease renewals, property files, custom fields). One full row per file, newest first.
- Added: `download_file` with three modes: `text` (returns the words, saves nothing), `link` (returns Rent Manager's own signed download link, saves nothing) and `save` (writes the file to `out/files/`). PDF text is extracted when `pdfplumber` or `pypdf` happens to be installed. Files stream to disk with a 2 GB limit; only `rentmanager.com` addresses are fetched, and the API token is never sent to the file server.
- Added: `locations` tool and a `location` argument on every tool, for companies with several Rent Manager locations (each is a separate database).
- Added: lease-audit guidance: a lease audit counts all rent charges plus rental assistance (RA) charges; notes appear on rent-roll results.
- Added: tool answers that save a file print `File: <full path>` and remind the model to show the file to the user.
- Added: files the server saves are deleted after 14 days (`RM_OUT_KEEP_DAYS`, `0` keeps everything).
- Changed: nested filters on history notes (such as `Tenant.TenantID`) are routed to the working per-parent route; a 404 "key not present" names the likely bad part; `describe_model` shows top-level filters first; one-to-many embeds become one row per child; lease start and end dates are default columns; scope lines name the filter that applies.
- Changed: `report_pdf` accepts a bare property ID. The experimental `embed` option was removed (no client showed the embedded PDF inline).

## [0.6.0-beta] - 2026-10-14
First public release. Unofficial, read-only, beta.
- 12 tools: `find_recipe`, `run_recipe`, `resolve`, `search_models`, `describe_model`, `enum_values`, `count`, `query`, `list_reports`, `report_info`, `run_report`, `report_pdf`.
- 24 recipes (vacancy, delinquency, aged receivables, rent roll, lease expirations, P&L, trial balance, GL, tenant ledger, collections and more) with live-tested parameters.
- `resolve` finds a property by name, short name, nickname, billing (LLC) name, street address or owner name, and asks instead of guessing when the match is weak.
- `report_pdf`: one PDF per property for any built-in report that supports it.
- Setup: `setup.py` (hidden password prompt, API structure download, index, Claude Desktop config merge), `doctor.py` health check, `tools/make_issue.py` scrubbed bug-report drafts.
- Safety: GET-only guard (plus the single login call), call log with masking, fan-out budget.
- Guides: `CLAUDE.md` (setup guide for a user's Claude), `docs/WHERE-IT-WORKS.md`, `KNOWN-ISSUES.md`.

Before this release the project went through internal development iterations (labelled v1 to v10 in private notes). They were never published and are not part of the public version history.

[Unreleased]: https://github.com/SZE2/rm-propertymanagement-mcp/compare/v0.10.0-beta...HEAD
[0.10.0-beta]: https://github.com/SZE2/rm-propertymanagement-mcp/releases/tag/v0.10.0-beta
