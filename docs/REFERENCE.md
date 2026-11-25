# Reference: tools, files, maintenance

Details for people who want to know how it works. Setup is in the [README](../README.md); the setup guide Claude follows is [CLAUDE.md](../CLAUDE.md).

## Use
Just ask. The server tells Claude its fast path:
1. `find_recipe` -> `run_recipe` (tested shortcuts: delinquency, aged receivables, vacant units, occupancy, lease expirations, rent roll, tenant ledger/open charges, P&L / NOI, trial balance, GL activity, move-ins/outs, charges, payments, owners, and bundles `property_snapshot`, `lender_package`).
2. Built-in reports: `list_reports` -> `report_info` -> `run_report` (one call covers all properties).
3. Anything else in the API: `search_models` -> `describe_model` -> `count` -> `query` (validated, paged in parallel, can group/sum).

`resolve` turns property nicknames into IDs (`resolve('')` lists them) and finds tenants/units/owners/vendors by name.
Every answer states its **Scope** (one property or all). Replies are size-capped (about 9,000 characters). A long result shows its first rows (only 10 when it has over 1,000 rows; set `show` to see more) and starts with a `MORE:` line with two ways to get the rest: `read_result(result_id, offset)` pages the full table as CSV through the MCP connection (works in every client, including ones that cannot see this computer's disk), or the saved file in `out/` (JSON + CSV) for agents whose file tools run on this computer. `RM_DELIVERY=mcp` hides the local paths (only `read_result` is offered), `RM_DELIVERY=file` offers only the path.

## Date filters and missing dates
`query` and `count` validate literal `null` values against the downloaded field types, including nested model paths. A `DateTime?` field can be absent in a returned row, but this does not make `EndDate,eq,null` a supported date filter: Rent Manager tries to parse the value as a date. These comparisons are rejected locally, including `ne` and null members of `in`, `ni` or `bt`. Use a real date such as `EndDate,ge,2026-01-01` instead.

To inspect missing dates, keep other supported server filters to narrow the scope, include the date in `fields`, and use `query(..., fields='EndDate', where='EndDate,eq,')` (empty value) locally. Use `where='EndDate,ne,'` for present dates. Local checks cover **only fetched rows**: a `CAPPED` or `PARTIAL` result cannot establish a complete missing-date count. Do not silently drop the date condition or substitute a sentinel date. Non-date filters and fields without type metadata retain their existing behavior; the server does not guess a type from the field name.

## PDF reports
`report_pdf(report, properties, parameters)` saves the built-in report as a PDF in `out/`, one file per property (all properties = one portfolio PDF), in parallel. Try: "PDF rent roll for Oak Court, Elm Park and Birch Lane". Read-only; if a report cannot render a PDF the reply says so and `run_report` gives the data instead.

## Files, locations
- `find_attachments(source, ids, ...)` lists files stored in Rent Manager (lease and lease-renewal PDFs, tenant/unit/owner/vendor/service-issue history files, property files, custom-field attachments), one row per file with a `file_ref`; `download_file(file_ref, mode)` gets one (text-like files come back as text, PDFs and other files are saved to `out/files/` for you to open, or `mode='link'` returns the signed link). Files contain tenant data. Downloaded files and old results in `out/` are deleted after 14 days (`RM_OUT_KEEP_DAYS`, 0 = never).
- `locations()` lists the locations (separate databases; IDs repeat). Every tool accepts `location='<name or id>'`.

## Skills (guides served over MCP)
The server also serves step-by-step guides for common Rent Manager workflows as Agent Skills (extension `io.modelcontextprotocol/skills`). A skill is a folder with a `SKILL.md` plus optional text files (`.md`, `.txt`, `.json`, `.csv`).
- Clients that support the extension call `skills/list` and `skills/get`. Every client can read a file with `resources/read` on `skill://<name>/<file>`, for example `skill://report-parameters-and-basis/SKILL.md`. Clients such as Hermes expose this as a `read_resource` tool.
- The guide URIs are listed at the end of the server instructions and appear in tool replies (concept pointers, `list_reports`). `resources/list` stays empty.
- Generic skills ship in `skills/`. Skills for your own installation go in `local/skills/` (never published); a public skill wins a name clash.
- Add a private skill: create `local/skills/<name>/SKILL.md` that starts with flat frontmatter, then the guide:
  ```
  ---
  name: <name>
  description: "Use when <trigger>. <one-line behaviour>."
  ---
  ```
  `<name>` is lowercase letters, digits and single hyphens (at most 64) and equals the folder name; the description is 1-1024 characters. Changes show on the next request; invalid skills are skipped with a note on stderr.
- `RM_MCP_SKILLS=0` turns skills off (no `resources` capability, no extension, no guide list in the instructions).

## MCP protocol versions
The server speaks both kinds of MCP clients over stdio:
- Clients that start with `initialize` (protocol 2024-11-05, 2025-03-26 or 2025-06-18) work as before. An unknown version in `initialize` gets 2025-06-18, including `2026-07-28`: that version does not use `initialize`, so a client that sends it there is answered as a 2025-06-18 client.
- Clients on protocol 2026-07-28 skip `initialize` and put `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` in every request's `_meta`. They can call `server/discover` first to get the supported versions, capabilities and instructions. Each result carries `resultType` and the server name and version in `_meta`; list, read and discover results also carry `ttlMs` (60000) and `cacheScope` (`private`).
- A request whose `_meta` names another version gets error `-32022` (Unsupported protocol version) with `data.supported` listing only `2026-07-28`, the one version served without `initialize`. Older versions are reached through `initialize`. A missing or invalid `clientCapabilities` gets `-32602`. `ping` and `logging/setLevel` do not exist in 2026-07-28 and get `-32601`.
- A request takes the new path when its `_meta` has `io.modelcontextprotocol/protocolVersion` set to anything other than 2024-11-05, 2025-03-26 or 2025-06-18. `initialize`, requests without that `_meta` key, and requests naming one of those three versions are answered as before.

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
`server.py` MCP server and tools - `mcp_skills.py` + `skills/` guides served as skills - `concepts.json` 'where is the data' pointers (rent, deposit, vacancy...) + `local/charge_types.json` rent/subsidy charge types (written by setup/doctor, editable) - `rm.py` GET-only API client + throttle - `spec_pull.py` / `spec_index.py` API structure + search index - `recipes/*.json` shortcuts - `setup.py`, `doctor.py` - `tests/`, `tools/` (issue drafting, public-tree builder).
Recipe format: `name, description, keywords, heavy, params{name: {type: date|int|number|bool|str|property|tenant, default, description}}, request: {report, parameters} | {query: {resource, filters[], fields, embeds, orderby}} | {bundle: [{recipe, params}]}, post: {where, rename, group_by, sum, sort, top, columns, running}`. Placeholders `{param}`; a filter/parameter whose placeholder is empty is dropped.

## Limits
500 API requests/minute (the server throttles at 7/s). Reports across a large portfolio can take 20-40 s; give a property for speed. Report IDs in recipes are standard Rent Manager IDs; `doctor.py` flags any recipe that does not match your install.
