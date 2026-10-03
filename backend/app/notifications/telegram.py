"""
Optional Telegram notifications — disabled until user configures and explicitly enables.
Credentials are NEVER exposed to the frontend.
"""

import structlog
from typing import Optional
from app.config import settings

logger = structlog.get_logger(__name__)


class TelegramNotifier:
    """
    Optional Telegram bot notifications.
    Disabled by default — only active when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
    are set in environment variables.
    """

    def __init__(self):
        self._enabled = settings.telegram_enabled
        self._bot = None

    async def initialize(self):
        """Initialize Telegram bot if configured."""
        if not self._enabled:
            logger.info("telegram_disabled", reason="No token/chat_id configured")
            return

        try:
            from telegram import Bot
            self._bot = Bot(token=settings.telegram_bot_token)
            logger.info("telegram_initialized")
        except Exception as e:
            logger.error("telegram_init_failed", error=str(e))
            self._enabled = False

    async def send_signal_alert(self, signal_data: dict):
        """Send a signal alert via Telegram."""
        if not self._enabled or not self._bot:
            return

        try:
            text = self._format_signal_message(signal_data)
            await self._bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode="HTML",
            )
            logger.info("telegram_signal_sent", signal_id=signal_data.get("signal_id"))
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e))

    def _format_signal_message(self, data: dict) -> str:
        """Format signal data as a Telegram message."""
        validated = "✅ VALIDATED" if data.get("is_validated") else "⚠️ RESEARCH ONLY"
        return (
            f"🔔 <b>Touch Signal {validated}</b>\n\n"
            f"📊 Signal ID: <code>{data.get('signal_id', 'N/A')}</code>\n"
            f"🎯 Direction: <b>{data.get('direction', 'N/A').upper()}</b>\n"
            f"📈 Instrument: {data.get('symbol', 'N/A')}\n"
            f"⏱ Duration: {data.get('duration_seconds', 540)}s\n"
            f"🎚 Barrier: {data.get('barrier_input', 'N/A')}\n"
            f"💰 Purchase: ${data.get('purchase_price', 'N/A')}\n"
            f"💵 Payout: ${data.get('total_payout', 'N/A')}\n"
            f"📊 Touch Prob: {data.get('calibrated_probability', 0):.2%}\n"
            f"📊 Breakeven: {data.get('breakeven_probability', 0):.2%}\n"
            f"💡 EV: ${data.get('ev_net', 0):.4f}\n\n"
            f"📝 {data.get('explanation', '')}\n\n"
            f"⚠️ {data.get('evidence_limitations', 'No guarantees.')}"
        )

    @property
    def enabled(self) -> bool:
        return self._enabled
