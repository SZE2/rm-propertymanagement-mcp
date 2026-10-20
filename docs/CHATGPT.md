# ChatGPT desktop and Codex setup (beta)

With ChatGPT, tenant and financial data returned by the tools goes to OpenAI as part of the chat; follow your company's data policy.

**BETA. UNOFFICIAL. Read-only.** This is the same local MCP server used by Claude; it runs on your computer. The local desktop/CLI connection below requires no hosting, OpenAI API key, or new Python packages. ChatGPT web uses a separate [Secure MCP Tunnel connection](#chatgpt-web-through-secure-mcp-tunnel), which requires an OpenAI runtime key. Web, mobile and cloud execution do not inherit the desktop connection.

## 1. Prepare Rent Manager

Use Python 3.9+ and a separate Rent Manager API user with **View privileges only**. Never ask for or paste a password in chat. The user types it into the hidden prompt of `setup.py` in their own terminal.

`PY` means `py -3` on Windows and `python3` on macOS/Linux. In the folder containing `server.py`, run:

```text
PY setup.py
PY doctor.py
```

The first setup downloads API structure, builds `spec/index.json` and creates local property aliases; it can take about ten minutes and is resumable. Fix doctor failures before relying on answers. The detailed prerequisites and privilege checks are also in [the setup guide](../CLAUDE.md#setup).

**Already using this server with Claude?** Your configured installation can be shared by both clients. Run the print command below in that installation; another setup or login is unnecessary. For development, a separate checkout needs its own setup before live calls. Do not register an unconfigured development copy expecting it to find another folder's credentials or API structure. Configuration and results are resolved relative to the server file, independent of the app's working directory.

## 2. Get the connection settings

```text
PY setup.py --print-chatgpt-config
```

This only prints settings; it does not read credentials, call Rent Manager or change either app. It prints the actual Python executable, absolute server path, working directory, a Codex command and a TOML alternative. Keep this output local because it contains your computer's paths.

## 3. Connect the desktop app

1. Open ChatGPT desktop **Settings > MCP servers > Add server**.
2. Use name `rentmanager`, connection type **STDIO**, and the command, argument and working directory printed above. The server path is one argument even when the folder has spaces. Do not paste the whole command line into the Command field.
3. Use a startup timeout of 20 seconds and a tool timeout of 180 seconds where those settings are exposed. Large reports can exceed the default 60 seconds.
4. Save, restart the MCP connection, and start a new local chat/task. Use `/mcp` to check whether `rentmanager` is connected and its tools are available.

Only add this entry after the user approves the exact settings. Preserve other MCP servers. Do not edit Claude's configuration for a ChatGPT connection.

### Codex CLI alternative

Run the exact `codex mcp add rentmanager -- ...` command printed by setup, then `codex mcp list`. These clients share MCP configuration on the same host. The CLI add command uses the default tool timeout; set `tool_timeout_sec = 180` in the resulting rentmanager table for large reports.

### TOML alternative

Back up `~/.codex/config.toml`, then merge the printed `[mcp_servers.rentmanager]` table. Keep other settings; update an existing rentmanager table rather than duplicating it. A trusted project may instead use `.codex/config.toml`. Do not put `.env` contents into either configuration. Setup prints valid escaped TOML even when a path contains spaces, backslashes or quotes.

## 4. Verify in the app

First test discovery without requesting business data:

> Using rentmanager, find a recipe for vacant units. Do not run it yet.

Expect a `find_recipe` tool call naming `vacant_units`, not a guessed answer. Then choose a property from your own installation and try:

- Which units are vacant at this property? Expect a live table and its Scope line.
- Who is behind on rent at this property? Expect a Scope line and balances from the returned data.
- Make a PDF rent roll for this property. Expect a saved file path. If the chat has file tools, have it open the file in a panel; otherwise open the path yourself.

Files stay on your computer. A saved path is not an uploaded attachment, and cloud chats cannot open a local path. Use `run_recipe` or `run_report` for questions about report contents; use `report_pdf` to export a PDF. Do not expose signed download links.

## Troubleshooting

- **No MCP servers setting or no tools in this chat:** check the installed app's local MCP support and select a local task. A cloud chat is a different connection path. Account/workspace policies can limit availability.
- **Process won't start:** use the exact Python executable and server argument printed by setup. On Windows, bare `python3` can be a Microsoft Store alias. Keep the folder in place after registering it.
- **Connected, but live calls fail:** run `doctor.py` in the registered server's folder. An unconfigured checkout can discover tools but cannot read live data.
- **Report times out:** confirm the rentmanager table has `tool_timeout_sec = 180` and restart the connection. This is the client timeout, separate from Rent Manager request timeouts.
- **Files aren't attached:** open the saved path using the client's local file tools or the computer's file manager. Do not claim an upload succeeded merely because a path was returned.

Offline compatibility tests: `PY -m unittest discover -s tests -p "test_unit_chatgpt.py"`. These verify the real stdio process and setup output without credentials. They do not prove that an app has been configured or that live Rent Manager calls work. Record the in-app checks separately before releasing desktop support.

### Verification recorded for this branch

On Windows, 2026-10-15, the installed OpenAI app-server from `codex-cli 0.162.1` connected to this server, discovered all 15 tools and called `find_recipe`. The same backend successfully ran scoped live vacancy and delinquency recipes and exported a PDF rent roll; the saved PDF bytes were verified. After user approval, the development installation was registered in the shared app configuration with a verified backup and preservation of all existing settings and servers. Discovery and the live checks passed again using the saved configuration, without test overrides or credential environment injection. The health check passed without warnings or failures. No model turn was run. The full unit suite (285 tests), issue/setup tooling checks and public-tree hygiene scan passed.

The live desktop conversation subsequently exposed all 15 rentmanager tools, and its model successfully called `find_recipe` for vacant units and received the `vacant_units` recipe. This verifies the installed connection in an actual desktop conversation, in addition to the backend live-data checks. Broader conversational accuracy and workflow evaluations remain release work. The separate web tunnel check is recorded below; mobile and Work Cloud execution have not been independently tested.

## ChatGPT web through Secure MCP Tunnel

This optional path keeps the MCP server on your computer. OpenAI's `tunnel-client` starts its own STDIO child process and connects outbound over HTTPS. It does not attach to an already-running Claude or Codex child. No public server, inbound firewall port, or change to Rent Manager transport is needed.

Use the [official tunnel setup guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and current `tunnel-client help quickstart`; product screens and CLI options can change. The tunnel ID identifies the route. The runtime API key authenticates the local tunnel program. Neither is the Rent Manager password. Browser sign-in is for setup and testing, not the long-running client's authentication.

### Setup checklist for the user and assisting agent

1. Complete Rent Manager setup and `doctor.py` in the exact installation the tunnel will run. Preserve the existing Claude registration and credentials.
2. In OpenAI Platform, select the intended organization and create or reuse a tunnel. **Associate it with the target ChatGPT workspace as well as its Platform organization, then save.** A blank workspace association can allow the local client to connect while ChatGPT denies plugin creation. Signing into the same email address alone does not establish this association.
3. Create or reuse a dedicated runtime API key. The runtime principal needs **Tunnels Read + Use**; tunnel creation/editing needs **Read + Manage**. Do not use an admin key for the running client. Restrict the runtime key to the required access where supported. Platform permissions and ChatGPT custom-plugin permissions are separate.
4. The user sets `CONTROL_PLANE_API_KEY` in their own terminal session using [the commands below](#enter-the-runtime-key-in-your-own-terminal), then runs the client from that same terminal. Reference it as `env:CONTROL_PLANE_API_KEY` in the profile. Never put the secret in command arguments, a committed file, chat, logs or screenshots. Hidden prompts may not accept paste in some terminals; a normal visible input prompt is an accepted fallback. Clear the variable after starting the managed runtime, or close the terminal. The child process retains its inherited copy; after a restart the key must be entered again.
5. Download the supported client from the official guide's latest-release link. Verify the published checksum for the downloaded archive. Keep binaries, profiles, runtime state and logs in a private ignored folder, such as `local/tunnel/`. `TUNNEL_CLIENT_STATE_DIR` can point to that folder's state subdirectory; this avoids editing app-wide configuration. Ask before writing outside the project.
6. Configure the STDIO target with the actual Python executable and this installation's absolute `server.py` path. Quote each path separately. On Windows, forward slashes inside the quoted command avoid backslash parsing surprises. The profile must contain a key reference, never the literal secret. Use a loopback health listener, preferably `127.0.0.1:0`, and a health URL file.
7. Run the read-only `tunnel-client admin tunnels get <tunnel-id>` with the runtime key supplied through `CONTROL_PLANE_API_KEY`; this checks credentials without using an admin key. Run `tunnel-client doctor --profile <profile> --profile-dir <private-profile-folder> --explain`. Doctor alone does not prove OpenAI accepted the key. Start a managed runtime with `tunnel-client runtimes connect --alias <alias> --tunnel-id <tunnel-id> --profile <profile> --profile-dir <private-profile-folder> --runtime-api-key env:CONTROL_PLANE_API_KEY --mcp-command '<quoted-python-and-server-command>'`. Use the current help for platform-specific quoting. Do not install the optional Codex tunnel plugin merely to make ChatGPT web work.
8. Check `runtimes status <alias> --json`, `/readyz`, and `/health?details=true`. Inspect selected status fields instead of dumping the entire status/log tail into chat. Confirm the process is running, healthy and ready, **and** the control-plane component has a successful poll with no repeating authentication errors. Local readiness alone can be green while OpenAI rejects the key. `/health/mcp` may remain `not_observed` until ChatGPT performs discovery.
9. In ChatGPT web, open **Plugins > Add > Add custom MCP server**. Choose **Tunnel**, enter the same tunnel ID and a descriptive name, and select **No authentication** for this server's current STDIO implementation. This means no additional MCP OAuth layer; OpenAI still authenticates and authorizes the tunnel. Review the warning, create the private plugin, and connect it. Do not publish it to the whole workspace as part of a personal setup.
10. Select the plugin in a web chat and ask: **Find a recipe for vacant units. Do not run it yet.** Verify an actual `find_recipe` call returns `vacant_units`. Only after this succeeds call the web connection verified. Next, the user chooses one property and tests live vacancy and delinquency against Rent Manager. Preserve Scope lines and quote returned numbers exactly.

### Enter the runtime key in your own terminal

PowerShell (hidden input; paste the key only when prompted):

```powershell
$runtimeKey = Read-Host 'Runtime API key' -AsSecureString
try {
    $env:CONTROL_PLANE_API_KEY = [System.Net.NetworkCredential]::new('', $runtimeKey).Password
} finally {
    $runtimeKey.Dispose()
    Remove-Variable runtimeKey
}
```

If hidden paste does not work, use `$env:CONTROL_PLANE_API_KEY = Read-Host 'Runtime API key (visible)'` instead. On macOS/Linux, regular terminal input is also acceptable:

```sh
printf 'Runtime API key (visible): '
IFS= read -r CONTROL_PLANE_API_KEY
export CONTROL_PLANE_API_KEY
```

Visible input can be seen on screen: do not record or screenshot the entry. Continue with the client commands in this same terminal. After the managed runtime starts, clear the terminal's copy with `Remove-Item Env:CONTROL_PLANE_API_KEY` in PowerShell or `unset CONTROL_PLANE_API_KEY` on macOS/Linux (or close the terminal). These commands do not persist the key in a file or user-level environment setting. Stop the managed runtime to discard its inherited process copy.

During Windows verification, a private Python/Tkinter launcher ran locally: the user pasted into its masked dialog and clicked Connect. It supplied `CONTROL_PLANE_API_KEY` for the metadata check and `RENTMANAGER_TUNNEL_API_KEY` for the managed runtime's environment reference. The secret stayed in launcher/child process memory, not a file or persistent user environment variable. That machine-specific launcher is not distributed; the terminal steps above are the reproducible alternative and need no helper script.

### Restart, limits and troubleshooting

- The computer must be on, awake and online, and the managed tunnel runtime must be running. Claude and Codex need not be open for this separate child process. Closing the setup window does not stop a managed runtime. This setup does not configure automatic startup after a reboot. Use the same alias to reconnect; stop with `tunnel-client runtimes stop <alias>`.
- `invalid_api_key`: stop the failed runtime and retry the complete secret once through the local input. A masked preview from the key list is not the secret. A successful metadata lookup distinguishes credential access from merely starting a local process.
- ChatGPT says the MCP server denied access: check that the tunnel's saved associations include the current ChatGPT workspace, then check the plugin operator's Tunnels Read + Use and workspace permissions. Keep the healthy client running while retrying.
- Files returned as local paths stay on the PC. A web chat cannot open them merely because MCP returned a path. PDF attachment delivery through the tunnel has not been verified; use returned report/recipe contents for analysis and do not expose signed download links.
- The tunnel has its own request deadline and size/rate limits. At verification, the official guide specified a 120-second end-to-end deadline; the desktop 180-second tool timeout cannot extend it. Check current official limits before relying on large reports. Creating an API key does not itself run model inference; API model usage and ChatGPT plan usage are separate. The guide does not establish a separate tunnel price, so do not promise zero ongoing cost.
- All callers to this installation use its configured Rent Manager identity. This is not per-employee Rent Manager authentication. Workspace-wide rollout needs a separate review of access, data handling and shared rate limits.

### Web verification recorded for this branch

On Windows, 2026-10-18, the official tunnel client v0.0.16 started this server as a STDIO child. After saving the missing ChatGPT workspace association, ChatGPT web created and connected the private plugin, discovered all 15 tools, and successfully called `find_recipe` for vacant units. The web response identified `vacant_units`; the local client recorded successful response delivery without failures or timeouts. This test requested no live business records. Scoped live web queries, web file delivery, mobile and Work Cloud execution remain separate checks. No Rent Manager data or transport code changed for the tunnel.

Connection details follow [OpenAI's MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=app). See [where it works](WHERE-IT-WORKS.md) for verification scope and [known issues](../KNOWN-ISSUES.md). File GitHub issues only after showing the user a scrubbed draft and obtaining approval.
