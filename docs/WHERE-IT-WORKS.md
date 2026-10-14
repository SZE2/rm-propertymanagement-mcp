# Where this works, and where it does not (beta)

This is a local, read-only MCP server. It runs as a program on YOUR computer. That decides everything below.

## Works (tested by the author on one Windows setup)
| Where you use Claude | Works? | Notes |
|---|---|---|
| Claude Desktop app, normal chat | Yes | After you register the server and restart the app. |
| Claude Desktop app, Cowork | Yes | Cowork runs on your computer, so it can reach the server. |
| Claude Code (desktop app Code tab or terminal) | Yes | Register with `claude mcp add`, or use the Desktop config. |
| Cowork from the Claude MOBILE app | Yes, while your computer is on | The mobile app sends the work to Cowork on your computer. The server still runs on the computer, not the phone. |

## Does NOT work
| Where | Why |
|---|---|
| A normal chat in the Claude MOBILE app | Cloud chats connect from Anthropic's servers and cannot reach a program on your computer. |
| claude.ai in a web browser | Same reason. |
| ChatGPT, Gemini, Grok and other assistants | This server is set up for Claude only. |
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
- A remote version for phone chats and claude.ai would need a hosted server with login. It is not part of this project yet.

## Found a problem?
Reports go through GitHub issues only (no email). Ask your Claude to file a bug or feature request for you: see [Bugs and feature requests](../CLAUDE.md#bugs-and-feature-requests-github-issues-only). It shows you the text first and removes names and numbers before sending.
