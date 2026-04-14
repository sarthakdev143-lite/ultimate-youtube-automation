import unittest
from datetime import UTC, datetime, timedelta

import telegram_bot


class TelegramBotHelpersTest(unittest.TestCase):
    def test_parse_schedule_iso_accepts_future_utc(self):
        future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        parsed = telegram_bot._parse_schedule_iso(future)
        self.assertGreater(parsed, datetime.now(UTC))

    def test_parse_schedule_iso_rejects_past(self):
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        with self.assertRaises(ValueError):
            telegram_bot._parse_schedule_iso(past)

    def test_parse_schedule_iso_rejects_invalid(self):
        with self.assertRaises(ValueError):
            telegram_bot._parse_schedule_iso("tomorrow 8pm")

    def test_runtime_status_contains_expected_fields(self):
        status = telegram_bot.get_telegram_runtime_status()
        self.assertIn("enabled", status)
        self.assertIn("running", status)
        self.assertIn("pending_chats", status)
        self.assertIn("restart_count", status)


if __name__ == "__main__":
    unittest.main()
