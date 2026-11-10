"""Telegram reminders for callback tasks in EspoCRM.

A callback is an ordinary EspoCRM Task: dateEnd is the call time, description is the note,
parent is the lead. The service polls the CRM once a minute and remembers what it has sent
in a small SQLite file, so a restart does not repeat messages.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

log = logging.getLogger("notifier")
OPEN = ["Not Started", "Started"]
EARLY = timedelta(minutes=10)
# After downtime, a callback that is more than this late is not announced any more.
LATE = timedelta(hours=1)
ESPO_FORMAT = "%Y-%m-%d %H:%M:%S"
HELP = ("Команды:\n/today — перезвоны на сегодня\n"
        "/remind ДД.ММ ЧЧ:ММ текст — новое напоминание\n"
        "Кнопка «Сделано» под уведомлением закрывает задачу в CRM.")


class Espo:
    def __init__(self, url, api_key, opener=None):
        self.url = url.rstrip("/") + "/api/v1/"
        self.api_key = api_key
        self.opener = opener or build_opener()

    def request(self, method, path, payload=None):
        req = Request(self.url + path, method=method,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"X-Api-Key": self.api_key, "Content-Type": "application/json",
                               "Accept": "application/json"})
        try:
            with self.opener.open(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            exc.close()
            raise RuntimeError(f"EspoCRM: HTTP {exc.code} на {method} {path.split('?')[0]}") from None

    def open_tasks(self):
        params = {"maxSize": 200, "orderBy": "dateEnd", "order": "asc",
                  "select": ["name", "status", "dateEnd", "dateEndDate", "description", "parentId", "parentType", "parentName",
                             "createdAt"],
                  "where": [{"type": "in", "attribute": "status", "value": OPEN}]}
        return self.request("GET", "Task?" + urlencode({"searchParams": json.dumps(params)})).get("list") or []

    def lead(self, lead_id):
        return self.request("GET", "Lead/" + lead_id)

    def complete(self, task_id):
        return self.request("PUT", "Task/" + task_id, {"status": "Completed"})

    def create_task(self, name, due_utc, note):
        return self.request("POST", "Task", {"name": name[:150], "dateEnd": due_utc.strftime(ESPO_FORMAT),
                                             "description": note, "status": "Not Started"})


class Telegram:
    def __init__(self, token, proxy=None, opener=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.opener = opener or build_opener(*([ProxyHandler({"http": proxy, "https": proxy})] if proxy else []))

    def call(self, method, http_timeout=30, **params):
        req = Request(self.base + method, data=json.dumps(params).encode(),
                      headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=http_timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            body = exc.read().decode(errors="replace")[:200]
            exc.close()
            # The token is part of the URL, so it is never included in the error text.
            raise RuntimeError(f"Telegram: HTTP {exc.code} на {method}: {body}") from None
        if not result.get("ok"):
            raise RuntimeError(f"Telegram: {method} вернул ошибку: {result.get('description')}")
        return result["result"]

    def send(self, chat_id, text, task_id=None):
        markup = {"inline_keyboard": [[{"text": "✅ Сделано", "callback_data": "done:" + task_id}]]} if task_id else None
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         disable_web_page_preview=True, **({"reply_markup": markup} if markup else {}))


class Sent:
    """Which reminders already went out; keyed by the due time so a moved callback is announced again."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, at TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()

    def has(self, key):
        return self.db.execute("SELECT 1 FROM sent WHERE key=?", (key,)).fetchone() is not None

    def add(self, key):
        self.db.execute("INSERT OR IGNORE INTO sent VALUES (?, ?)", (key, datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def get(self, name, default=None):
        row = self.db.execute("SELECT value FROM state WHERE name=?", (name,)).fetchone()
        return row[0] if row else default

    def set(self, name, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (name, str(value)))
        self.db.commit()


def parse_due(task):
    if not task.get("dateEnd"):
        return None
    return datetime.strptime(task["dateEnd"], ESPO_FORMAT).replace(tzinfo=timezone.utc)


def parse_remind(text, now_local):
    """'/remind 12.11 15:30 позвонить Ольге' -> (aware local datetime, note) or None."""
    match = re.match(r"^/remind(?:@\w+)?\s+(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s+(.+)$", text.strip(), re.S)
    if not match:
        return None
    day, month, hour, minute, note = int(match[1]), int(match[2]), int(match[3]), int(match[4]), match[5].strip()
    try:
        due = now_local.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    if due < now_local - timedelta(days=1):  # 05.01 written in December means next January.
        try:
            due = due.replace(year=due.year + 1)
        except ValueError:
            return None
    return due, note


class Notifier:
    def __init__(self, espo, telegram, sent, chat_id, tz, public_url, digest_at="09:30", clock=None):
        self.espo, self.telegram, self.sent = espo, telegram, sent
        self.chat_id = str(chat_id or "")
        self.tz = tz
        self.public_url = public_url.rstrip("/")
        self.digest_at = tuple(int(x) for x in digest_at.split(":"))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.leads = {}

    def local(self, moment):
        return moment.astimezone(self.tz)

    def lead_info(self, task):
        if task.get("parentType") != "Lead" or not task.get("parentId"):
            return None
        if task["parentId"] not in self.leads:
            try:
                self.leads[task["parentId"]] = self.espo.lead(task["parentId"])
            except RuntimeError as exc:
                log.warning("Лид %s не прочитан: %s", task["parentId"], exc)
                self.leads[task["parentId"]] = None
        return self.leads[task["parentId"]]

    def card(self, task, title):
        due = parse_due(task)
        lines = [f"<b>{html.escape(title)}</b>"]
        if due:
            lines[0] += f" · {self.local(due):%H:%M}"
        lead = self.lead_info(task)
        if lead:
            company = lead.get("accountName") or task.get("parentName") or ""
            lines.append("🏢 " + html.escape(company))
            contact = " ".join(x for x in (lead.get("firstName"), lead.get("lastName")) if x).strip()
            if contact and contact != company:
                lines.append("👤 " + html.escape(contact))
            phones = [p.get("phoneNumber") for p in lead.get("phoneNumberData") or [] if p.get("phoneNumber")]
            phones = phones or ([lead["phoneNumber"]] if lead.get("phoneNumber") else [])
            for phone in phones[:3]:
                # Telegram turns a bare phone number into a tap-to-call link; tel: anchors are not allowed.
                lines.append("📞 " + html.escape(phone))
        else:
            lines.append("📝 " + html.escape(task.get("name") or ""))
        if task.get("description"):
            lines.append("💬 " + html.escape(task["description"]))
        if lead:
            lines.append(f'<a href="{self.public_url}/#Lead/view/{lead["id"]}">Открыть лида в CRM</a>')
        return "\n".join(lines)

    def day_label(self, due):
        today = self.local(self.clock()).date()
        day = self.local(due).date()
        word = "сегодня" if day == today else "завтра" if day == today + timedelta(days=1) else f"{day:%d.%m}"
        return f"{word} в {self.local(due):%H:%M}"

    def confirm_new(self, task, now):
        """Tell right away which day a callback set in the CRM landed on; the date is easy to miss there."""
        due = parse_due(task)
        created = task.get("createdAt")
        if not due or task.get("parentType") != "Lead" or not created or due <= now + EARLY:
            return False
        created = datetime.strptime(created, ESPO_FORMAT).replace(tzinfo=timezone.utc)
        key = f"{task['id']}:new:{task['dateEnd']}"
        if now - created > timedelta(minutes=15) or self.sent.has(key):
            return False
        lead = self.lead_info(task) or {}
        company = lead.get("accountName") or task.get("parentName") or ""
        text = f"📌 Перезвон назначен: <b>{html.escape(company)}</b> — {self.day_label(due)}"
        if task.get("description"):
            text += "\n💬 " + html.escape(task["description"])
        self.telegram.send(self.chat_id, text)
        self.sent.add(key)
        log.info("Подтверждён новый перезвон %s", task["id"])
        return True

    def check_due(self):
        now = self.clock()
        self.leads = {}
        sent = 0
        for task in self.espo.open_tasks():
            self.confirm_new(task, now)
            due = parse_due(task)
            if not due or now < due - EARLY or now > due + LATE:
                continue
            kind = "now" if now >= due else "soon"
            key = f"{task['id']}:{kind}:{task['dateEnd']}"
            if self.sent.has(key):
                continue
            if kind == "now" or not self.sent.has(f"{task['id']}:now:{task['dateEnd']}"):
                title = "Пора перезвонить" if kind == "now" else "Через 10 минут перезвон"
                self.telegram.send(self.chat_id, self.card(task, title), task["id"])
                log.info("Отправлено напоминание (%s) по задаче %s", kind, task["id"])
                sent += 1
            self.sent.add(key)
        return sent

    def today_tasks(self):
        today = self.local(self.clock()).date()
        items = []
        for task in self.espo.open_tasks():
            due = parse_due(task)
            if due and self.local(due).date() == today:
                items.append((self.local(due).strftime("%H:%M"), task))
            elif not due and task.get("dateEndDate") == today.isoformat():
                items.append(("весь день", task))
        return items

    def today_text(self, header="Перезвоны на сегодня"):
        items = self.today_tasks()
        if not items:
            return f"<b>{header}</b>\nНа сегодня перезвонов нет."
        self.leads = {}
        lines = [f"<b>{header}</b> ({len(items)})"]
        for when, task in items:
            lead = self.lead_info(task)
            who = (lead or {}).get("accountName") or task.get("parentName") or task.get("name") or ""
            phone = (lead or {}).get("phoneNumber") or ""
            note = (task.get("description") or "").split("\n")[0][:80]
            lines.append(f"\n<b>{when}</b> {html.escape(who)}" + (f"\n📞 {html.escape(phone)}" if phone else "")
                         + (f"\n💬 {html.escape(note)}" if note else ""))
        return "\n".join(lines)

    def check_digest(self):
        local_now = self.local(self.clock())
        if (local_now.hour, local_now.minute) < self.digest_at:
            return False
        key = "digest:" + local_now.date().isoformat()
        if self.sent.has(key):
            return False
        self.telegram.send(self.chat_id, self.today_text("Доброе утро! Перезвоны на сегодня"))
        self.sent.add(key)
        log.info("Отправлен список на %s", local_now.date())
        return True

    def handle_update(self, update):
        query = update.get("callback_query")
        message = update.get("message") or {}
        chat = str(((query or {}).get("message") or message).get("chat", {}).get("id", ""))
        if not self.chat_id:
            if message.get("text", "").startswith("/start"):
                self.telegram.call("sendMessage", chat_id=chat,
                                   text=f"Ваш chat_id: {chat}\nВставьте его в .env в строку TELEGRAM_CHAT_ID= и перезапустите notifier.")
            return
        if chat != self.chat_id:
            log.warning("Сообщение из чужого чата проигнорировано")
            return
        if query:
            return self.handle_button(query)
        text = (message.get("text") or "").strip()
        if text.startswith("/today"):
            self.telegram.send(self.chat_id, self.today_text())
        elif text.startswith("/remind"):
            parsed = parse_remind(text, self.local(self.clock()))
            if not parsed:
                self.telegram.send(self.chat_id, "Формат: /remind 12.11 15:30 что сделать")
                return
            due, note = parsed
            task = self.espo.create_task("Напоминание: " + note.split("\n")[0], due.astimezone(timezone.utc), note)
            self.telegram.send(self.chat_id, f"Напоминание создано на {due:%d.%m %H:%M}.\n{html.escape(note)}")
            log.info("Создана задача %s из Telegram", task.get("id"))
        else:
            self.telegram.send(self.chat_id, HELP)

    def handle_button(self, query):
        data = query.get("data") or ""
        if not data.startswith("done:"):
            return
        task_id = data[5:]
        try:
            self.espo.complete(task_id)
        except RuntimeError as exc:
            log.warning("Задача %s не закрыта: %s", task_id, exc)
            self.telegram.call("answerCallbackQuery", callback_query_id=query["id"], text="Не получилось отметить в CRM")
            return
        self.telegram.call("answerCallbackQuery", callback_query_id=query["id"], text="Отмечено в CRM")
        msg = query.get("message") or {}
        if msg.get("message_id"):
            self.telegram.call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=msg["message_id"],
                               reply_markup={"inline_keyboard": [[{"text": "✅ Выполнено", "callback_data": "noop"}]]})
        log.info("Задача %s отмечена выполненной", task_id)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    api_key = os.environ.get("NOTIFIER_API_KEY", "").strip()
    if not token or not api_key:
        log.warning("Не задан TELEGRAM_BOT_TOKEN или NOTIFIER_API_KEY в .env — уведомления выключены.")
        while True:
            time.sleep(3600)
    tz = ZoneInfo(os.environ.get("NOTIFIER_TZ", "Asia/Yekaterinburg"))
    telegram = Telegram(token, os.environ.get("TELEGRAM_PROXY") or None)
    notifier = Notifier(Espo(os.environ.get("ESPO_URL", "http://espocrm"), api_key), telegram,
                        Sent(os.environ.get("NOTIFIER_DATA", "/data") + "/sent.sqlite3"),
                        os.environ.get("TELEGRAM_CHAT_ID", "").strip(), tz,
                        os.environ.get("ESPO_PUBLIC_URL", "http://localhost:8080"),
                        os.environ.get("NOTIFIER_DIGEST_TIME", "09:30"))
    me = telegram.call("getMe")
    log.info("Бот @%s запущен, часовой пояс %s, chat_id %s", me.get("username"), tz.key,
             "задан" if notifier.chat_id else "НЕ задан — напишите боту /start")
    offset = int(notifier.sent.get("offset", 0))
    last_check = 0.0
    while True:
        try:
            if notifier.chat_id and time.monotonic() - last_check >= 60:
                last_check = time.monotonic()
                notifier.check_due()
                notifier.check_digest()
            for update in telegram.call("getUpdates", http_timeout=40, offset=offset, timeout=25):
                offset = update["update_id"] + 1
                notifier.sent.set("offset", offset)
                notifier.handle_update(update)
        except (RuntimeError, URLError, TimeoutError, OSError) as exc:
            log.warning("%s", exc)
            time.sleep(10)


if __name__ == "__main__":
    main()
