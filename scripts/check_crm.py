"""Exercise an installed CRM, using an isolated SQLite store and synthetic leads.

Run: docker compose exec -T collectors python - < scripts/check_crm.py
The local workspace needs no credentials. Created records are removed.
"""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from leadgen.crm import EspoClient, sync
from leadgen.model import normalize
from leadgen.store import Store


class InstalledCrmTest(unittest.TestCase):
    def setUp(self):
        self.client = EspoClient()
        self.prefix = "CRM test " + uuid.uuid4().hex[:12]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "check.sqlite3")
        self.addCleanup(self.remove_leads)

    def admin(self, method, path, payload=None):
        request = Request(self.client.url + path, method=method,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json",
                                   "X-Skip-Duplicate-Check": "true"})
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else None

    def find_leads(self):
        leads = self.admin("GET", "Lead?" + urlencode({"searchParams": json.dumps({"where": [
            {"type": "equals", "attribute": "accountName", "value": self.prefix}], "maxSize": 50})}))["list"]
        if any(lead.get("accountName") != self.prefix for lead in leads):
            raise RuntimeError("CRM ignored the test cleanup filter; refusing to modify unrelated leads.")
        return leads

    def remove_leads(self):
        # Find by the unique test name even if POST committed but its reply was lost.
        for lead in self.find_leads():
            self.admin("DELETE", "Lead/" + lead["id"])

    def record(self, **changes):
        return normalize({"source": "yandex", "source_id": self.prefix,
                          "source_url": "https://yandex.ru/maps/org/123456789/",
                          "name": self.prefix, "city": "Тестовый город", "address": "Тестовая, 1",
                          "phone": "+79990000001", "email": "crm-test@example.invalid",
                          "site": "https://example.invalid/", "rating": 4.6, "reviews": 42, **changes})

    def assert_synced(self, count):
        result = sync(self.store, self.client)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["synced"], count)
        self.assertEqual(result["pending"], 0)

    def test_import_enrichment_branches_and_manager_changes(self):
        local_id = self.store.ingest(self.record())
        self.store.ingest(normalize({"source": "2gis", "source_id": self.prefix + "-2gis",
                                     "source_url": "https://2gis.ru/moscow/firm/123456789",
                                     "name": self.prefix, "city": "Тестовый город", "address": "Тестовая, 1",
                                     "rating": 4.2, "reviews": 19}))
        self.assert_synced(1)
        leads = self.find_leads()
        self.assertEqual(len(leads), 1)
        lead_id = leads[0]["id"]
        remote = self.admin("GET", "Lead/" + lead_id)
        self.assertEqual(remote["cLeadgenId"], local_id)
        self.assertEqual(remote["cWebsiteStatus"], "Present")
        self.assertEqual(remote["cYandexUrl"], "https://yandex.ru/maps/org/123456789/")
        self.assertEqual(remote["cYandexRating"], 4.6)
        self.assertEqual(remote["cYandexReviews"], 42)
        self.assertEqual(remote["cGisRating"], 4.2)
        self.assertEqual(remote["cGisReviews"], 19)
        self.assertEqual(remote["phoneNumberData"][0]["phoneNumber"], "+79990000001")
        self.assertEqual(remote["emailAddressData"][0]["emailAddress"], "crm-test@example.invalid")

        user_id = self.admin("GET", "App/user")["user"]["id"]
        phones, emails = remote["phoneNumberData"], remote["emailAddressData"]
        phones[0]["optOut"] = emails[0]["optOut"] = True
        phones[0]["type"] = "Personal"
        self.admin("PUT", "Lead/" + lead_id, {
            "status": "In Process", "description": "Тест: перезвонить завтра",
            "assignedUserId": user_id, "website": "https://manager.example.invalid/",
            "phoneNumberData": phones, "emailAddressData": emails, "doNotCall": True,
        })
        merged = self.store.ingest(self.record(source="2gis", source_id=self.prefix + "-gis",
            source_url="https://2gis.ru/moscow/firm/123456789", email="new@example.invalid"))
        self.assertEqual(merged, local_id)
        self.assert_synced(1)
        # Force a replay even though this SQLite revision is already acknowledged.
        self.assertEqual(self.client.upsert(self.store.companies()[0]), lead_id)
        self.assert_synced(0)
        remote = self.admin("GET", "Lead/" + lead_id)
        self.assertEqual(remote["status"], "In Process")
        self.assertEqual(remote["description"], "Тест: перезвонить завтра")
        self.assertEqual(remote["assignedUserId"], user_id)
        self.assertEqual(remote["website"], "https://manager.example.invalid/")
        self.assertTrue(remote["doNotCall"])
        self.assertTrue(remote["phoneNumberData"][0]["optOut"])
        self.assertEqual(remote["phoneNumberData"][0]["type"], "Personal")
        self.assertTrue(next(e for e in remote["emailAddressData"]
                             if e["emailAddress"] == "crm-test@example.invalid")["optOut"])
        self.assertEqual(len(json.loads(remote["cMapSources"])), 3)
        self.assertEqual(remote["cGisUrl"], "https://2gis.ru/moscow/firm/123456789")

        self.store.ingest(self.record(source_id=self.prefix + "-branch", address="Тестовая, 2"))
        self.assert_synced(1)
        self.assertEqual(len(self.find_leads()), 2)

    def test_manual_company_and_task(self):
        lead = self.admin("POST", "Lead", {"accountName": self.prefix, "status": "New"})
        task = self.admin("POST", "Task", {"name": self.prefix + " follow-up",
                         "parentType": "Lead", "parentId": lead["id"], "status": "Not Started"})
        self.addCleanup(self.admin, "DELETE", "Task/" + task["id"])
        saved = self.admin("GET", "Task/" + task["id"])
        self.assertEqual(saved["parentType"], "Lead")
        self.assertEqual(saved["parentId"], lead["id"])
        self.assertIsNone(saved.get("assignedUserId"))
        self.admin("PUT", "Task/" + task["id"], {"status": "Completed"})
        self.assertEqual(self.admin("GET", "Task/" + task["id"])["status"], "Completed")

    def test_workspace_settings_and_import_fields(self):
        settings = self.admin("GET", "Settings")
        self.assertEqual(settings["tabList"][:2], ["Lead", "Task"])
        self.assertEqual(settings["tabList"][2]["text"], "Сбор компаний")
        self.assertEqual(settings["quickCreateList"], ["Lead", "Task"])
        metadata = self.admin("GET", "Metadata")
        self.assertEqual(metadata["clientDefs"]["App"]["loginView"], "custom:views/local-workspace")
        self.assertFalse(metadata["entityDefs"]["Task"]["fields"]["assignedUser"]["required"])
        fields = metadata["entityDefs"]["Lead"]["fields"]
        for name in ("cLeadgenId", "cMapSources", "cYandexUrl", "cGisUrl",
                     "cYandexRating", "cYandexReviews", "cGisRating", "cGisReviews"):
            with self.subTest(field=name):
                self.assertTrue(fields[name]["clientReadOnly"])
                self.assertFalse(fields[name].get("readOnly", False))

    def test_integration_role_cannot_delete_or_access_accounts(self):
        for method, path in (("GET", "Account"), ("DELETE", "Lead/nonexistent")):
            with self.subTest(method=method, path=path):
                with self.assertRaises(HTTPError) as caught:
                    # Use the actual transport to retain the HTTP status for this assertion.
                    request = Request(self.client.url + path, method=method,
                                      headers={"X-Api-Key": self.client.api_key})
                    urlopen(request, timeout=30).close()
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()

    def test_panel_opens_without_credentials_and_rejects_cross_site_writes(self):
        panel = os.environ.get("LEADGEN_PANEL_URL", "http://localhost:8090")
        with urlopen(panel + "/api/status", timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertNotIn("WWW-Authenticate", response.headers)
            self.assertIn("jobs", json.load(response))
        request = Request(panel + "/api/jobs", method="POST", data=b"{}",
                          headers={"Content-Type": "application/json", "Origin": "https://example.invalid"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=30).close()
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
