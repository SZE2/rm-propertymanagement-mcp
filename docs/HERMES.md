# Hermes Agent setup (beta)

The same local, read-only STDIO server used by Claude and ChatGPT; [Hermes Agent](https://hermes-agent.nousresearch.com/docs/) launches it as a child process.
Tenant and financial data returned by the tools goes to your Hermes model provider as part of the chat; follow your company's data policy.

1. Do the Rent Manager setup first (`PY setup.py`, `PY doctor.py`; see [CLAUDE.md](../CLAUDE.md#setup)). The user types the password, never the agent.
2. `PY setup.py --print-hermes-config` prints the exact command and YAML for this computer (no credentials, writes nothing).
3. Register with the printed `hermes mcp add rentmanager ...`, or merge the YAML into the profile's `config.yaml`. Use `hermes -p <profile> mcp add ...` for a non-default profile.
4. `hermes mcp test rentmanager` should list all 15 tools. Start a new session; tools register at startup and appear as `mcp_rentmanager_<tool>`.
5. Ask: "Using rentmanager, find a recipe for vacant units. Do not run it yet." Expect a `find_recipe` call naming `vacant_units`.

## Notes
- `timeout: 180` is already set in the printed settings (large reports can take 20-40 s); the server throttles itself at 7 requests/s.
- Hermes gives MCP child processes a filtered environment; the server reads `.env` from its own folder, so credentials never go into Hermes config.
- PDFs from `report_pdf` are saved to `out/`; deliver them from a messaging chat with `MEDIA:<absolute path>`.
- If the agent's file tools run in a Docker sandbox, mount `out/` read-only into the container so the agent can post-process saved results.
- All callers share one Rent Manager identity. For a team gateway, restrict who can reach the agent (allowlist or pairing) and keep it read-only.
- Python 3.9 or newer works. If you also edit the registration by hand, use the same interpreter that ran `setup.py`.
