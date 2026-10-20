# Developing this repo

PY = `py -3` on Windows, `python3` on macOS/Linux.

## Tests
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
Most unit tests read the API structure that `setup.py` downloads into `spec/`. On a fresh clone, run `PY setup.py` first (about 10 minutes). Without it about 17 tests fail with `spec/index.json missing`. That is expected, not a bug. Making them skip cleanly is on the to-do list.
