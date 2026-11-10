import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from notifier.bot import Notifier, Sent, parse_remind

TZ = ZoneInfo("Asia/Yekaterinburg")
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)  # 15:00 in Orenburg


def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


class FakeEspo:
    def __init__(self, tasks):
        self.tasks = tasks
        self.completed = []
        self.created = []

    def open_tasks(self):
        return [t for t in self.tasks if t["id"] not in self.completed]

    def lead(self, lead_id):
        return {"id": lead_id, "accountName": "Тестовый салон", "firstName": "Иван", "lastName": "",
                "phoneNumberData": [{"phoneNumber": "+79990001122"}]}

    def complete(self, task_id):
        self.completed.append(task_id)

    def create_task(self, name, due_utc, note):
        self.created.append((name, due_utc, note))
        return {"id": "new"}


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.calls = []

    def send(self, chat_id, text, task_id=None):
        self.messages.append((chat_id, text, task_id))

    def call(self, method, **params):
        self.calls.append((method, params))


class NotifierTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "sent.sqlite3"
        self.now = NOW
        self.task = {"id": "t1", "name": "Перезвон: Тестовый салон", "dateEnd": stamp(NOW + timedelta(minutes=10)),
                     "description": "Спросить про сертификаты", "parentType": "Lead", "parentId": "l1"}
        self.espo = FakeEspo([self.task])
        self.telegram = FakeTelegram()
        self.bot = self.make()

    def make(self):
        return Notifier(self.espo, self.telegram, Sent(self.path), "42", TZ, "http://localhost:8080",
                        clock=lambda: self.now)

    def test_reminds_ten_minutes_before_and_on_time_once(self):
        self.now = NOW - timedelta(minutes=1)
        self.assertEqual(self.bot.check_due(), 0)
        self.now = NOW
        self.assertEqual(self.bot.check_due(), 1)
        self.assertEqual(self.bot.check_due(), 0)
        self.make().check_due()  # A restart does not repeat it.
        self.now = NOW + timedelta(minutes=10)
        self.assertEqual(self.bot.check_due(), 1)
        self.assertEqual(self.bot.check_due(), 0)
        texts = [m[1] for m in self.telegram.messages]
        self.assertIn("Через 10 минут", texts[0])
        self.assertIn("Пора перезвонить", texts[1])
        self.assertIn("Тестовый салон", texts[1])
        self.assertIn("Иван", texts[1])
        self.assertIn("+79990001122", texts[1])
        self.assertIn("Спросить про сертификаты", texts[1])
        self.assertIn("http://localhost:8080/#Lead/view/l1", texts[1])
        self.assertIn("15:10", texts[1])  # Orenburg time.
        self.assertEqual(self.telegram.messages[1][2], "t1")  # Carries the «Сделано» button.

    def test_new_callback_is_confirmed_with_its_day(self):
        self.task["dateEnd"] = stamp(NOW + timedelta(days=1))
        self.task["createdAt"] = stamp(NOW - timedelta(minutes=1))
        self.bot.check_due()
        self.bot.check_due()
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertIn("завтра в 15:00", self.telegram.messages[0][1])
        self.assertIn("Тестовый салон", self.telegram.messages[0][1])

    def test_old_callbacks_are_not_confirmed(self):
        self.task["dateEnd"] = stamp(NOW + timedelta(days=1))
        self.task["createdAt"] = stamp(NOW - timedelta(hours=1))
        self.bot.check_due()
        self.assertEqual(self.telegram.messages, [])

    def test_moved_callback_is_announced_again(self):
        self.bot.check_due()
        self.task["dateEnd"] = stamp(NOW + timedelta(minutes=5))
        self.assertEqual(self.bot.check_due(), 1)

    def test_old_callback_after_downtime_is_not_announced(self):
        self.now = NOW + timedelta(hours=3)
        self.assertEqual(self.bot.check_due(), 0)

    def test_html_in_note_is_escaped(self):
        self.task["description"] = "<b>скидка</b> & подарок"
        self.bot.check_due()
        self.assertIn("&lt;b&gt;скидка&lt;/b&gt; &amp; подарок", self.telegram.messages[0][1])

    def test_daily_digest_once_after_0930(self):
        self.now = datetime(2026, 11, 12, 4, 29, tzinfo=timezone.utc)  # 09:29 local
        self.assertFalse(self.bot.check_digest())
        self.now = datetime(2026, 11, 12, 4, 30, tzinfo=timezone.utc)
        self.assertTrue(self.bot.check_digest())
        self.assertFalse(self.bot.check_digest())
        self.assertIn("15:10", self.telegram.messages[0][1])
        self.assertIn("Тестовый салон", self.telegram.messages[0][1])

    def test_today_command_ignores_other_days(self):
        self.espo.tasks.append({"id": "t2", "name": "Завтра", "dateEnd": stamp(NOW + timedelta(days=1))})
        self.bot.handle_update({"message": {"chat": {"id": 42}, "text": "/today"}})
        text = self.telegram.messages[0][1]
        self.assertIn("(1)", text)
        self.assertNotIn("Завтра", text)

    def test_remind_command_creates_task_in_utc(self):
        self.bot.handle_update({"message": {"chat": {"id": 42}, "text": "/remind 13.11 09:15 позвонить клиенту"}})
        name, due, note = self.espo.created[0]
        self.assertEqual(due, datetime(2026, 11, 13, 4, 15, tzinfo=timezone.utc))
        self.assertEqual(note, "позвонить клиенту")
        self.assertIn("13.11 09:15", self.telegram.messages[0][1])

    def test_done_button_completes_task(self):
        self.bot.handle_update({"callback_query": {"id": "q", "data": "done:t1",
                                                   "message": {"message_id": 7, "chat": {"id": 42}}}})
        self.assertEqual(self.espo.completed, ["t1"])
        self.assertEqual(self.telegram.calls[0][0], "answerCallbackQuery")

    def test_other_chats_are_ignored(self):
        self.bot.handle_update({"message": {"chat": {"id": 7}, "text": "/today"}})
        self.bot.handle_update({"callback_query": {"id": "q", "data": "done:t1", "message": {"chat": {"id": 7}}}})
        self.assertEqual((self.telegram.messages, self.espo.completed), ([], []))

    def test_without_chat_id_start_tells_the_id(self):
        bot = Notifier(self.espo, self.telegram, Sent(self.path), "", TZ, "http://x", clock=lambda: self.now)
        bot.handle_update({"message": {"chat": {"id": 555}, "text": "/start"}})
        self.assertIn("555", self.telegram.calls[0][1]["text"])


class ParseRemindTest(unittest.TestCase):
    def test_formats(self):
        now = datetime(2026, 12, 20, 12, 0, tzinfo=TZ)
        self.assertEqual(parse_remind("/remind 05.01 08:00 с Новым годом", now)[0], datetime(2027, 1, 5, 8, 0, tzinfo=TZ))
        self.assertIsNone(parse_remind("/remind 32.01 08:00 x", now))
        self.assertIsNone(parse_remind("/remind завтра", now))


if __name__ == "__main__":
    unittest.main()
