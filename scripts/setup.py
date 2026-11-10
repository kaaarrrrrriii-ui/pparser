"""Create local credentials once. Never replace an existing installation's secrets."""
import os
import secrets
from pathlib import Path

from customize_crm import main as customize

ROOT = Path(__file__).resolve().parent.parent
customize()
target = ROOT / ".env"
# Added later than the first release: appended to an existing .env without touching other lines.
OPTIONAL = {"NOTIFIER_API_KEY": lambda: secrets.token_hex(24), "TELEGRAM_BOT_TOKEN": str, "TELEGRAM_CHAT_ID": str,
            "TELEGRAM_PROXY": str}
if target.exists():
    present = {line.split("=", 1)[0].strip() for line in target.read_text(encoding="utf-8").splitlines() if "=" in line}
    missing = [key for key in OPTIONAL if key not in present]
    if missing:
        text = target.read_text(encoding="utf-8")
        with target.open("a", encoding="utf-8") as f:
            f.write(("" if text.endswith("\n") or not text else "\n") +
                    "\n".join(f"{key}={OPTIONAL[key]()}" for key in missing) + "\n")
        print("В .env добавлены строки: " + ", ".join(missing) + " (значения не показаны).")
    print(".env уже существует; пароли сохранены. Настройки CRM обновлены.")
else:
    values = {
        "ADMIN_USER": "admin", "ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "DB_PASSWORD": secrets.token_urlsafe(24), "DB_ROOT_PASSWORD": secrets.token_urlsafe(32),
        "ESPO_API_KEY": secrets.token_hex(24),
        "ESPO_PUBLIC_URL": "http://localhost:8080", "LEADGEN_PUBLIC_URL": "http://localhost:8090",
        **{key: make() for key, make in OPTIONAL.items()},
    }
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print("Создан .env со служебными секретами (права 0600). Локальный интерфейс открывается без входа.")
print("Запуск: docker compose up -d --build")
print("Сбор компаний: http://localhost:8090 · CRM: http://localhost:8080")
