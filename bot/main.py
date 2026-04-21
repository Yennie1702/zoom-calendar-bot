"""JA Scheduler Bot — Telegram entry point.

Run locally:
    python -m bot.main

Render deploy: Web Service (free tier) — requires $PORT binding within 60s,
so we run a minimal HTTP health server alongside the long-polling bot.
"""
from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import config, handlers

log = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("OK — JA Scheduler Bot is running\n".encode("utf-8"))

    def log_message(self, *args, **kwargs) -> None:  # silence request spam
        return


def _start_health_server() -> None:
    """Bind to $PORT (set by Render) so Render marks the service healthy.

    Runs in a daemon thread so it dies with the main process. Skipped locally
    when $PORT is unset — saves a socket if chị chạy bot ở terminal.
    """
    port_str = os.environ.get("PORT")
    if not port_str:
        log.info("No $PORT set → skipping health server (local dev mode)")
        return
    port = int(port_str)
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)  # noqa: S104
    t = threading.Thread(target=server.serve_forever, daemon=True, name="health")
    t.start()
    log.info("Health server listening on 0.0.0.0:%d", port)


def main() -> None:
    config.setup_logging()
    log.info("Starting JA Scheduler Bot…")
    log.info("  allowed_chat_id = %s", config.TELEGRAM_ALLOWED_CHAT_ID)
    log.info("  calendar_account = %s", config.GOOGLE_CALENDAR_ACCOUNT)
    log.info("  google_ready = %s", config.google_ready())

    _start_health_server()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("list", handlers.cmd_list))
    app.add_handler(CommandHandler("sync", handlers.cmd_sync))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback))

    log.info("Bot is polling…")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
