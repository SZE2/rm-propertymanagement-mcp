# RM PropertyManagement MCP (unofficial)

Ask your AI assistant questions about your **Rent Manager** data and get live answers:

> "Who is behind on rent at Oak Court?" - "Which units are vacant?" - "Make a PDF rent roll for these three properties." - "Find the lease PDF for unit 204 and check the rent."

It is a small program that runs **on your own computer** and lets Claude or ChatGPT look at your Rent Manager account. It can only **read**, never change anything.

> **Beta, unofficial.** Not made, endorsed or supported by Rent Manager. Answers can be wrong: check important numbers in Rent Manager. Known limits: [KNOWN-ISSUES.md](KNOWN-ISSUES.md).

## 1. Pick your AI

| You use | Works? | Start with |
|---|---|---|
| **Claude** Desktop chat, **Cowork** (also from the Claude phone app while your computer is on), **Claude Code** | Yes | [Path A: Claude](#path-a-claude-anthropic) |
| **ChatGPT** desktop app, **Codex** | Yes | [Path B: ChatGPT and Codex](#path-b-chatgpt-and-codex-openai) |
| **ChatGPT web** | Advanced, through OpenAI's tunnel | [docs/CHATGPT.md](docs/CHATGPT.md#chatgpt-web-through-secure-mcp-tunnel) |
| claude.ai in a browser, the normal Claude phone chat | Not yet | [Why: docs/WEB-CHAT.md](docs/WEB-CHAT.md) |
| Gemini, Grok, others | Not set up | |

More detail on what works and what is untested: [docs/WHERE-IT-WORKS.md](docs/WHERE-IT-WORKS.md).

## 2. What you need first
- **Rent Manager API access.** It is not on by default and may cost extra. Ask Rent Manager support (apisupport@rentmanager.com).
- **A separate Rent Manager user** with API access and **View-only** privileges (Properties, Units, Customers, Leases, Charges, Payments, Owners, Reports, accounting reads). Never use an admin or your own login. Rent Manager's guide: "API Enabled User Account Setup".
- Your **company code**: the `<company>` in `https://<company>.api.rentmanager.com`.
- **Python 3.9+** on your computer. (`git` is optional: you can download a ZIP instead.)

## 3. Get the code
Either let your AI do it (see the prompts below) or do it yourself:

```
git clone https://github.com/SZE2/rm-propertymanagement-mcp.git
```

No git? On this page click **Code > Download ZIP** and unzip it. A ZIP has no updates: to update, download it again and copy your `.env` and `local/` folders over.

## 4. Run the setup (you type the password, nobody else)
Open PowerShell (Windows) or Terminal (macOS) **in the folder** and run:

```
py -3 setup.py        # Windows
python3 setup.py      # macOS / Linux
```

(Below, **PY** means `py -3` on Windows and `python3` on macOS/Linux. On Windows never type bare `python3`: it is often a Microsoft Store stub that does nothing.)

It asks for your company code, username and password (hidden), tests the login, downloads the structure of the Rent Manager API (about 10 minutes the first time; if it stops, run it again and it continues), builds a search index and a list of your properties, then runs a health check. **Never type your password into a chat.** Setup registers nothing in your AI apps by itself.

## 5. Connect your AI

### Path A: Claude (Anthropic)
Open this folder in Claude (Claude Desktop: the **Cowork** or **Code** tab, choose the folder; or run `claude` in a terminal inside it) and say:

> **Follow CLAUDE.md to set this up for me.**

Claude checks your setup, runs the health check, and walks you through connecting. Or do it yourself:
- **Claude Desktop / Cowork:** `PY setup.py --merge-desktop-config` (shows the change, asks y/n, backs up your config), then fully quit and reopen the app.
- **Claude Code:** `claude mcp add rentmanager -- "<python>" "<folder>/server.py"` (`PY setup.py --print-config` prints the exact command).

### Path B: ChatGPT and Codex (OpenAI)
Open this folder in Codex (or tell your assistant) and say:

> **Read AGENTS.md and docs/CHATGPT.md, and set this up for me.**

Or do it yourself: `PY setup.py --print-chatgpt-config` prints the exact settings for your computer. Then follow [docs/CHATGPT.md](docs/CHATGPT.md): ChatGPT desktop (Settings > MCP servers), the Codex command, or the web tunnel.

## 6. Try it
Start a **new chat** with the connection on and ask, using a property from your own list (`local/aliases.json`):
1. "Which units are vacant at {property}?"
2. "Who is behind on rent at {property}? Show balance and days late."
3. "Make a PDF rent roll for {property}."

If a tool does not appear, the app was not fully restarted. In manual permission mode the app asks before every tool call; switch to Auto, or allow the `rentmanager` tools.

## Safe by design
- **Read-only.** Only GET requests to Rent Manager (plus the login). No tool changes anything.
- **Live data.** Business data is never cached; only the API structure is stored.
- **Stays on your computer.** Your login lives in `.env`; saved results and files go in `out/` and are deleted after 14 days. Nothing is uploaded by this project.
- **Your AI provider sees what the tools return** (tenant and owner names, amounts), as part of the chat. Follow your company's data policy. Optional masking of phone, email and SSN fields: set `RM_MASK_PII=1`.
- **Every answer states its scope** (one property or all).

## Problems or ideas
Use **GitHub issues only** (no email). Ask your AI to file one: it writes a scrubbed draft (no names or amounts), shows it to you, and files it only after you say yes.

## More
[Reference: tools, files, maintenance](docs/REFERENCE.md) - [Changelog](CHANGELOG.md) - [Known issues](KNOWN-ISSUES.md) - [Where it works](docs/WHERE-IT-WORKS.md) - [Why no claude.ai web chat](docs/WEB-CHAT.md) - [For maintainers](docs/MAINTAINING.md)

License: MIT (see LICENSE). Rent Manager is a trademark of its owner; this project is not affiliated with it.
