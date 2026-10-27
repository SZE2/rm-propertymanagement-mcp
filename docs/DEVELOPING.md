# Developing this repo

PY = `py -3` on Windows, `python3` on macOS/Linux.

## Tests
- All offline suites, with a process-wide network guard: `PY -S tools/test_offline.py`. No setup, credentials, private API spec or third-party packages are required. This runs unit, issue-tool and hygiene tests, not the live tests.
- Offline unit tests: `PY -m unittest discover -s tests -p "test_unit*.py"`
- Setup/env/issue tool tests: `PY tests/test_issue_tools.py`
- Desktop/Codex compatibility without credentials or `spec/`: `PY -m unittest discover -s tests -p "test_unit_chatgpt.py"`. Uses the real stdio process from an unrelated working directory. Manual desktop connection and conversational/live checks are separate release checks in `docs/CHATGPT.md`.
- Hygiene: `PY tests/test_hygiene.py` (add `--root DIR` to scan another folder, e.g. a public tree). It fails on company codes, real API hosts, home-directory paths, money figures, property names from `local/aliases.json` and strings from the untracked `local/blocklist.txt`. In a git checkout only files that are tracked AND listed in `tools/publish_manifest.txt` are scanned.
- Live tests (`tests/test_mcp.py`, `tests/test_recipes.py`) need a working `.env` and hit the real API with GET only.

## Rules
- Read-only: GET only (plus the single login POST in `rm.py`). Do not add tools that change Rent Manager.
- No company, property, tenant or personal data in tracked files. `spec/`, `.env`, `.token_cache.json`, `local/`, `out/` are gitignored.
- No pip dependencies: Python standard library only.
- Credentials: `.env` values may be double-quoted (setup.py quotes values with quotes, spaces, `#`, `$` or backslash); `rm.py` reads them back exactly, and `.env` wins over RM_* variables in the OS environment.

## Publishing
`PY tools/make_public_tree.py --out DIR` copies exactly the files in `tools/publish_manifest.txt` into an empty folder and scans it with the hygiene test. A private `CLAUDE.local.md` (untracked) may hold personal rules.

## Running the unit tests on a fresh clone
Run `PY -m unittest discover -s tests -p "test_unit*.py"` directly. Do **not** run `setup.py` for offline testing or copy an installation's `spec/` into the checkout.

Spec-dependent modules explicitly import `setUpModule` from `tests/offline_fixture.py`. It writes temporary source files from the hand-authored `tests/fixtures/offline_spec.json`, builds a real index using `spec_index.build(verify=False)`, and redirects spec/model loading for that module. Index caches, local aliases, charge families, output and token paths are isolated and restored afterward. Unexpected `urlopen` calls fail. The fixture is used even when a developer has a private spec; it is never a server fallback or a complete API reference. Normal server operation still requires setup-generated metadata.

Keep fixtures small and synthetic. Add contract fields from the public code/recipes and test expectations, never by copying private API metadata or business records. Keep API replies stubbed, but leave routing, validation, report lookup and index loading real where those are the behavior under test. A missing fixture entry should fail a test, not skip it. Module setup works with discovery and direct execution (for example `PY tests/test_unit_v11.py`).

The HistoryNotes routing regression uses a real synthetic index and model validation, then asserts the stubbed GET paths and remaining filters. With no index, resource lookup fails before routing; mocking only `_query_one` had hidden this missing prerequisite, not reproduced a production routing defect.

`-S` excludes site packages to prove standard-library-only operation. The existing optional YAML round-trip test skips without PyYAML; on Python before 3.11 the existing TOML parser test also skips. Hygiene skips private-input checks when their inputs are absent. These are not spec-dependent skips. No package installation is needed.

GitHub Actions runs the offline checks on Python 3.9, 3.11 and 3.14, then builds/scans the manifest-only public tree and repeats the suites there. To repeat the export check locally, run `PY tools/make_public_tree.py --out DIR` with an empty output directory, change into that directory and run `PY -S tools/test_offline.py`. Do not use broad unittest discovery: `test_mcp.py` and `test_recipes.py` are live checks.
