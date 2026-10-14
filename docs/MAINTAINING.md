# Maintaining this repo

For people (and their AI) who change the code or cut releases. Users who only want to set the tool up should read `CLAUDE.md` instead.

## Who reads what
| You are | Read |
|---|---|
| A user setting up or using the tool, or the user's Claude | `CLAUDE.md` |
| A contributor or maintainer, or an AI working on the code | this file, then `docs/DEVELOPING.md` |
| An AI agent that looks for `AGENTS.md` | `AGENTS.md` (points to the two above) |

## Rules that never change
- Read-only: GET only (plus the one login POST). Never add a tool that changes Rent Manager data.
- Never ask for, store in chat or commit a password, token, company code, property name, tenant name, email, phone number or dollar amount. `tests/test_hygiene.py` and `tools/make_public_tree.py` enforce this on what gets published.
- Python standard library only. No new dependency without a very good reason.
- Bug reports and feature requests come through GitHub issues only.

## Versioning
Semantic Versioning, in the `0.MINOR.PATCH` form while the project is in beta, with an optional `-beta` label (example: `0.7.0-beta`).
- **MINOR** (0.6 -> 0.7): new tool, new option, changed output format, anything a user or a Claude could notice.
- **PATCH** (0.7.0 -> 0.7.1): bug fixes only, no new behavior.
- **1.0.0**: when the setup guide has been run end to end on macOS and Windows by someone other than the author, the persona eval sets pass, and the known-issues list holds only minor items.
- The single source of truth is `VERSION` in `server.py`. The server reports it to the client, and `doctor.py` should print it.
- Git tags are `v<version>` (example `v0.7.0-beta`). Every tag has a GitHub Release.

## Cutting a release (checklist)
1. All tests pass: `PY -m unittest discover -s tests -p "test_unit*.py"` and `PY tests/test_hygiene.py`.
2. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new heading `## [x.y.z-beta] - YYYY-MM-DD`; add a fresh empty `[Unreleased]`; update the compare links at the bottom.
3. Set `VERSION` in `server.py` to the same string.
4. Commit: `Release x.y.z-beta`.
5. Tag and push: `git tag vx.y.z-beta && git push origin main --tags`.
6. Create the GitHub Release (mark pre-release while the version starts with 0): `gh release create vx.y.z-beta --prerelease --title "x.y.z-beta" --notes-file <(the changelog section)>`.
7. Add anything newly learned that users must know to `KNOWN-ISSUES.md`.

## Working on issues
- Label `bug` or `enhancement`. Ask for the `doctor.py` output and the server version if missing.
- Reports are scrubbed by `tools/make_issue.py`; do not ask reporters for names or amounts.
- Fix on `main` with a small commit that mentions the issue number.

## What stays out of the repo
`.env`, `local/`, `out/`, `spec/` (generated per Rent Manager install), `CLAUDE.local.md`, private dev logs. See `tools/publish_manifest.txt` for the exact public file list.
