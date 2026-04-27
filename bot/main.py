"""JA Scheduler Bot — Telegram entry point.

Two runtime modes:

LOCAL (`python -m bot.main`)
    No $PORT env → long-polling (getUpdates loop).

RENDER (Web Service, $PORT set)
    Webhook mode — PTB's `run_webhook` binds to $PORT and accepts Telegram
    POSTs at `/telegram`. This keeps the service "awake" on Render Free
    (incoming HTTP = activity) and avoids the outbound-polling sleep trap
    where `run_polling` blocks but Render spins the worker down.

RENDER_EXTERNAL_URL (e.g. `https://ja-scheduler-bot.onrender.com`) is set
automatically by Render; we use it to `setWebhook(...)` on startup.
"""
from __future__ import annotations

import logging
import os

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import config, handlers, scheduler

log = logging.getLogger(__name__)


def _build_app() -> Application:
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(scheduler.start_background_tasks)
        .build()
    )
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("list", handlers.cmd_list))
    app.add_handler(CommandHandler("sync", handlers.cmd_sync))
    app.add_handler(CommandHandler("today", handlers.cmd_today))
    app.add_handler(CommandHandler("members", handlers.cmd_members))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))
    app.add_handler(CallbackQueryHandler(handlers.handle_callback))
    return app


def main() -> None:
    config.setup_logging()
    log.info("Starting JA Scheduler Bot…")
    log.info("  allowed_chat_id = %s", config.TELEGRAM_ALLOWED_CHAT_ID)
    log.info("  calendar_account = %s", config.GOOGLE_CALENDAR_ACCOUNT)
    log.info("  google_ready = %s", config.google_ready())

    app = _build_app()
    port_str = os.environ.get("PORT")

    if port_str:
        port = int(port_str)
        # Render sets RENDER_EXTERNAL_URL automatically (e.g. https://xxx.onrender.com)
        external_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")
        if not external_url:
            raise RuntimeError(
                "PORT set but RENDER_EXTERNAL_URL/WEBHOOK_URL missing — "
                "cannot configure Telegram webhook"
            )
        webhook_url = f"{external_url.rstrip('/')}/telegram"
        log.info("Webhook mode — binding 0.0.0.0:%d, webhook=%s", port, webhook_url)
        app.run_webhook(
            listen="0.0.0.0",  # noqa: S104
            port=port,
            url_path="telegram",
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
    else:
        log.info("No $PORT → polling mode (local dev)")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )


if __name__ == "__main__":
    main()
