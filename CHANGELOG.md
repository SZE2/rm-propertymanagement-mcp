# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [Semantic Versioning](https://semver.org/) in its `0.MINOR.PATCH` form (see [docs/MAINTAINING.md](docs/MAINTAINING.md)). While the version starts with 0, anything may change between releases.

## [Unreleased]
Planned for 0.7.0-beta:
- Added: `find_attachments` and `download_file` tools (history-note, lease and lease-renewal, property-file and custom-field attachments), a `locations` tool and a per-call `location` argument, so lease PDFs and other files can be found and saved across Rent Manager locations.
- Changed: queries on tenant history notes are routed to the working per-tenant route; nested lists (such as renewal attachments) show as readable rows; lease start and end dates are default columns; scope lines name the filter that applies.

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

[Unreleased]: https://github.com/SZE2/rm-propertymanagement-mcp/compare/v0.6.0-beta...HEAD
[0.6.0-beta]: https://github.com/SZE2/rm-propertymanagement-mcp/releases/tag/v0.6.0-beta
