# Agents: start here

This repo has two audiences. Pick yours:

- **The user wants ChatGPT desktop/web or Codex setup**: read `docs/CHATGPT.md` and follow the appropriate local or tunnel steps.
- **The user wants Hermes Agent setup**: read `docs/HERMES.md` and follow it.
- **The user wants Claude setup** (for example "set this up for me" in Claude): read `CLAUDE.md` and follow it step by step.
- **You are changing the code or cutting a release**: read `docs/MAINTAINING.md`, then `docs/DEVELOPING.md`.

Always, for both:
1. Read-only. Only GET requests to Rent Manager (plus the single login call). Never add anything that changes data.
2. Never ask for, accept or repeat a password in chat. The user types it into the hidden prompt of `setup.py`.
3. No real names, amounts, company codes or paths in anything you commit or file as an issue.
4. Ask the user before editing anything outside this folder (such as the Claude Desktop config) and before filing a GitHub issue.
