# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/) in its `0.MINOR.PATCH` form (see [docs/MAINTAINING.md](docs/MAINTAINING.md)). While the version starts with 0, anything may change between releases.

## [Unreleased]

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

[Unreleased]: https://github.com/SZE2/rm-propertymanagement-mcp/compare/v0.7.1-beta...HEAD
[0.7.1-beta]: https://github.com/SZE2/rm-propertymanagement-mcp/compare/v0.7.0-beta...v0.7.1-beta
[0.7.0-beta]: https://github.com/SZE2/rm-propertymanagement-mcp/compare/v0.6.0-beta...v0.7.0-beta
[0.6.0-beta]: https://github.com/SZE2/rm-propertymanagement-mcp/releases/tag/v0.6.0-beta
