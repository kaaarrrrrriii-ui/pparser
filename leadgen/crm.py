from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .franchise import chain_reason, domain_cities


class EspoHttpError(RuntimeError):
    def __init__(self, status, method):
        self.status = status
        self.method = method
        super().__init__(f"EspoCRM: HTTP {status}. Проверьте доступ API, поля и права роли.")


class LeadRemoved(RuntimeError):
    """The lead was sent earlier but no longer exists in the CRM: a manager deleted it."""


REMOVED_REASON = "Удалён из CRM вручную, повторно не отправляется"


class EspoClient:
    def __init__(self, url=None, api_key=None, sleep=time.sleep):
        self.url = (url or os.environ.get("ESPO_URL", "http://localhost:8080")).rstrip("/") + "/api/v1/"
        self.api_key = api_key or os.environ.get("ESPO_API_KEY", "")
        self.sleep = sleep
        if not self.api_key:
            raise ValueError("Не задан ESPO_API_KEY")

    def request(self, method, path, payload=None):
        req = Request(self.url + path, method=method,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"X-Api-Key": self.api_key, "Content-Type": "application/json", "Accept": "application/json",
                               # Branches may legitimately share an email/name. cLeadgenId has a unique DB index.
                               **({"X-Skip-Duplicate-Check": "true"} if method in ("POST", "PUT") else {})})
        # POST is deliberately not retried: the server may already have committed it.
        attempts = 3 if method in ("GET", "PUT") else 1
        for attempt in range(attempts):
            try:
                with urlopen(req, timeout=30) as response:
                    return json.load(response)
            except HTTPError as exc:
                exc.close()
                if exc.code not in (429, 502, 503, 504) or attempt == attempts - 1:
                    raise EspoHttpError(exc.code, method) from None
                retry_after = exc.headers.get("Retry-After", "")
                self.sleep(min(120, max(2 ** attempt, int(retry_after) if retry_after.isdigit() else 0)))
            except (URLError, TimeoutError, OSError):
                if attempt == attempts - 1:
                    raise RuntimeError("EspoCRM недоступна. Лиды сохранены; повторите синхронизацию.") from None
                self.sleep(2 ** attempt)

    def upsert(self, row):
        result = self.request("GET", "Lead?" + urlencode({"searchParams": json.dumps({"where": [
            {"type": "equals", "attribute": "cLeadgenId", "value": row["id"]}], "maxSize": 2})}))
        matches = result.get("list") or []
        if len(matches) > 1 or any(item.get("cLeadgenId") != row["id"] for item in matches):
            raise RuntimeError("EspoCRM вернула неоднозначное совпадение cLeadgenId. Импорт остановлен.")
        existing = matches[0] if matches else None
        if existing:
            existing = self.request("GET", "Lead/" + existing["id"])
            if existing.get("cLeadgenId") != row["id"]:
                raise RuntimeError("В EspoCRM изменился cLeadgenId найденного лида. Импорт остановлен.")
        if not existing and row.get("crm_id"):
            # Recreating it would bring back a lead the manager removed (and the CRM keeps the
            # deleted row's unique cLeadgenId, so the insert fails anyway).
            raise LeadRemoved(REMOVED_REASON)
        data = row["data"]
        payload = {
            "cLeadgenId": row["id"], "cWebsiteStatus": data["website_status"],
            "cMapSources": json.dumps(data["sources"], ensure_ascii=False),
            "cSocialLinks": "\n".join(data["social"]), "cCategory": data["category"][:255],
            "cYandexUrl": next((s["url"] for s in data["sources"] if s["source"] == "yandex"), ""),
            "cGisUrl": next((s["url"] for s in data["sources"] if s["source"] == "2gis"), ""),
        }
        metrics = data.get("source_metrics", {})
        for source, fields in (("yandex", ("cYandexRating", "cYandexReviews")),
                               ("2gis", ("cGisRating", "cGisReviews"))):
            values = metrics.get(source, {})
            for key, value in zip(fields, (values.get("rating"), values.get("review_count"))):
                if value is not None:
                    payload[key] = value
        if not existing:
            payload.update({"firstName": "", "lastName": data["name"][:100], "accountName": data["name"][:255],
                            "status": "New", "source": "Maps"})
        if data.get("gis_created_at"):
            payload["cGisCreatedAt"] = data["gis_created_at"]
        # Protect manager changes and workflow: only fill missing ordinary fields.
        for key, value in {"website": next(iter(data["websites"]), ""), "addressStreet": data["address"],
                           "addressCity": data["city"]}.items():
            if value and not (existing or {}).get(key):
                payload[key] = value
        for key, values, attr, extra in (("phoneNumberData", data["phones"], "phoneNumber", {"type": "Office"}),
                                         ("emailAddressData", data["emails"], "emailAddress", {})):
            current = list((existing or {}).get(key) or [])
            known = {str(x.get(attr, "")).lower() for x in current}
            for value in values:
                if value.lower() not in known:
                    current.append({attr: value, "primary": not current, **extra})
                    known.add(value.lower())
            if current:
                payload[key] = current
        if (existing or {}).get("website"):
            payload["cWebsiteStatus"] = "Present"
        return self.request("PUT" if existing else "POST",
                            "Lead/" + existing["id"] if existing else "Lead", payload)["id"]


def sync(store, client=None):
    client = client or EspoClient()
    errors = []
    count = 0
    filtered = 0
    cities = domain_cities(store.companies())
    for row in store.companies(pending=True):
        # A lead already in the CRM may hold manager work, so it is never withheld afterwards.
        if row.get("removed_from_crm"):
            store.filtered(row["id"], row["revision"], row["error"] or REMOVED_REASON)
            filtered += 1
            continue
        reason = "" if row["crm_id"] else chain_reason(row["data"], cities)
        if reason:
            store.filtered(row["id"], row["revision"], reason)
            filtered += 1
            continue
        try:
            crm_id = client.upsert(row)
            store.synced(row["id"], row["revision"], crm_id)
            count += 1
        except LeadRemoved as exc:
            store.removed(row["id"], row["revision"], str(exc))
            filtered += 1
        except Exception as exc:
            store.sync_error(row["id"], str(exc))
            errors.append({"id": row["id"], "error": str(exc)})
            # Invalid records stay pending without blocking the rest of the batch.
            # Stop on server, network and access failures to avoid hammering an unavailable CRM.
            if not (isinstance(exc, EspoHttpError) and exc.method in ("POST", "PUT")
                    and exc.status in (400, 409, 422)):
                break
    return {"synced": count, "filtered": filtered, "errors": errors, "pending": len(store.companies(pending=True))}
