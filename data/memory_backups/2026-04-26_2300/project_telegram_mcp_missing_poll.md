---
name: Telegram MCP lacks inbound polling
description: telegram-bot MCP server exposes only outbound tools — blocks autonomous intake loop
type: project
originSessionId: 36ac9666-a011-4849-ad3c-5096ece59cba
---
The `telegram-bot` MCP server (bot MBOsMentor_bot, id 8617767176) as configured on 2026-04-19 exposes only three tools: `get_me`, `send_message`, `send_markdown`. There is no `poll_telegram_updates` / getUpdates / webhook tool.

**Why:** The autonomous sales-pipeline intake agent spec (as invoked by chị Yến) requires `poll_telegram_updates(offset=...)` for its STEP 1. Without inbound polling, the loop cannot read customer messages and terminates immediately.

**How to apply:** If asked to run the autonomous Telegram intake loop again, first verify an inbound polling tool is available via ToolSearch. If still missing, notify chị Yến in her private DM (chat_id 8173041182) rather than silently looping. The MCP server needs to be extended before the agent can run.
