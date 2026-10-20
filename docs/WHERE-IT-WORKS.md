# Where this works, and where it does not (beta)

This is a local, read-only MCP server. It runs as a program on YOUR computer. That decides everything below.

## Works (tested by the author on one Windows setup)
| Where you use Claude | Works? | Notes |
|---|---|---|
| Claude Desktop app, normal chat | Yes | After you register the server and restart the app. |
| Claude Desktop app, Cowork | Yes | Cowork runs on your computer, so it can reach the server. |
| Claude Code (desktop app Code tab or terminal) | Yes | Register with `claude mcp add`, or use the Desktop config. |
| Cowork from the Claude MOBILE app | Yes, while your computer is on | The mobile app sends the work to Cowork on your computer. The server still runs on the computer, not the phone. |

## ChatGPT desktop and Codex (0.8.0-beta)

The server supports a local STDIO connection with ChatGPT desktop and Codex. [Setup and verification](CHATGPT.md) includes exact connection settings, longer report timeouts and local file handling. A configured Claude installation can be shared.

Verified on Windows, 2026-10-15: the installed OpenAI app-server (`codex-cli 0.162.1`) discovered all 15 tools, called `find_recipe`, ran scoped live vacancy and delinquency recipes, and exported a PDF rent roll whose bytes were verified. The development installation was then registered with user approval; the same checks passed from the saved configuration, and its health check had no warnings or failures. A live desktop conversation subsequently exposed all 15 tools and successfully called `find_recipe` for vacant units. Offline tests also cover startup and error responses without credentials. Broader conversational accuracy evaluations remain release work. The separate web tunnel check is described below; mobile and Work Cloud execution have not been independently tested.

## ChatGPT web through Secure MCP Tunnel (0.8.0-beta)

Verified on Windows, 2026-10-18: ChatGPT web connected a private plugin through the official tunnel client, discovered all 15 tools and called `find_recipe`, returning `vacant_units`. Follow the [tunnel setup checklist](CHATGPT.md#chatgpt-web-through-secure-mcp-tunnel), including the saved ChatGPT workspace association. Scoped live web queries, file delivery, mobile and Work Cloud execution have not been independently verified. Keep the PC awake and online and the separate tunnel runtime running; no automatic restart has been configured.

## Does NOT work
| Where | Why |
|---|---|
| A normal chat in the Claude MOBILE app | Cloud chats connect from Anthropic's servers and cannot reach a program on your computer. |
| claude.ai in a web browser | Same reason. Why, and what it would take: [WEB-CHAT.md](WEB-CHAT.md). |
| ChatGPT web/mobile or cloud execution using only this local configuration | These do not inherit the local desktop connection. Remote access is a separate integration. |
| Gemini, Grok and other assistants | No setup guide or client verification is provided. |
| Any use when your computer is off or asleep | The server is a process on that computer. |

## What must be true for it to work
- Your computer is on, awake, online, and the Claude Desktop app is running (for phone use through Cowork).
- You created a separate read-only, API-enabled Rent Manager user (see [SETUP in CLAUDE.md](../CLAUDE.md#setup)).
- Rent Manager API access is enabled for your company. It may cost extra; ask Rent Manager.
- Python 3.9 or newer and git are installed.

## Not tested yet
- macOS and Linux (the code is plain Python and should work; the config file paths differ).
- Claude Desktop installed outside the Windows Store.
- Companies with more than one Rent Manager location (one location was tested).
- Very large portfolios: portfolio-wide reports can take 20 to 40 seconds.

## Limits to expect
- Beta software. Answers can be wrong. Check important numbers in Rent Manager.
- Read-only: it cannot change anything in Rent Manager.
- Rent Manager allows 500 API requests a minute. Heavy questions are throttled.
- Tenant and owner data you ask for is sent to your AI provider as part of the chat. Follow your company's data policy. Optional masking: set `RM_MASK_PII=1`.
- Not affiliated with or endorsed by Rent Manager.
- The private ChatGPT tunnel is a separate integration; it does not establish support for normal Claude phone chats or claude.ai.

## Found a problem?
Reports go through GitHub issues only (no email). Ask your Claude to file a bug or feature request for you: see [Bugs and feature requests](../CLAUDE.md#bugs-and-feature-requests-github-issues-only). It shows you the text first and removes names and numbers before sending.
