import tempfile
import unittest
from pathlib import Path

from leadgen.crm import REMOVED_REASON, EspoClient, LeadRemoved, sync
from leadgen.franchise import chain_reason, city_section, domain_cities
from leadgen.model import normalize
from leadgen.store import Store


def row(city, *sites):
    return {"data": {"city": city, "websites": list(sites)}}


class DomainFilterTest(unittest.TestCase):
    def test_city_section_of_a_brand_site(self):
        for url in ("https://orenburg.brand.ru/", "https://oren.brand.ru/", "https://brand.ru/orenburg/",
                    "https://brand.ru/city/orenburg", "https://orb.brand.info/", "https://brand.ru/oren",
                    "https://n1.yclients.com/select-city/116/select-branch"):
            self.assertTrue(city_section(url, "Оренбург"), url)

    def test_local_domains_are_kept(self):
        for url in ("https://orenburg-stom.ru/", "https://stom56.ru/", "https://clinic.orenburg.ru/",
                    "https://orenburg.taplink.cc/", "https://brand.ru/orenburg-news", "https://art.orange.ru/x",
                    "https://brand.ru/ore", "https://reacentr-orenburg.ru/"):
            self.assertFalse(city_section(url, "Оренбург"), url)

    def test_shared_domain_needs_two_cities(self):
        rows = [row("Оренбург", "https://net.ru/"), row("Оренбург", "https://net.ru/b"),
                row("Оренбург", "https://local.ru/"), row("Казань", "https://net.ru/"),
                row("Казань", "https://rutube.ru/a"), row("Оренбург", "https://rutube.ru/b")]
        cities = domain_cities(rows)
        self.assertIn("net.ru", chain_reason(rows[0]["data"], cities))
        self.assertEqual(chain_reason(rows[2]["data"], cities), "")
        self.assertEqual(chain_reason(rows[4]["data"], cities), "")

    def test_no_site_is_kept(self):
        self.assertEqual(chain_reason(row("Оренбург")["data"], {}), "")


class Client:
    def __init__(self):
        self.sent = []

    def upsert(self, company):
        self.sent.append(company["data"]["name"])
        return "crm-" + company["id"]


class SyncFilterTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "t.sqlite3")

    def add(self, name, site, city="Оренбург"):
        return self.store.ingest(normalize({"source": "2gis", "source_id": name, "name": name, "city": city,
                                            "address": name + " 1", "site": site}))

    def test_chain_stays_local_and_is_not_retried(self):
        self.add("Сеть", "https://orenburg.brand.ru/")
        self.add("Местная", "https://local.ru/")
        client = Client()
        result = sync(self.store, client)
        self.assertEqual(client.sent, ["Местная"])
        self.assertEqual((result["synced"], result["filtered"], result["pending"]), (1, 1, 0))
        withheld = next(c for c in self.store.companies() if c["data"]["name"] == "Сеть")
        self.assertIsNone(withheld["crm_id"])
        self.assertIn("orenburg.brand.ru", withheld["error"])

    def test_lead_already_in_crm_keeps_syncing(self):
        company = self.add("Старая", "https://net.ru/")
        client = Client()
        sync(self.store, client)
        self.add("Другая", "https://net.ru/", city="Казань")
        self.store.ingest(normalize({"source": "2gis", "source_id": "Старая", "name": "Старая", "city": "Оренбург",
                                     "address": "Старая 1", "site": "https://net.ru/", "phone": "+79990000009"}))
        client.sent.clear()
        sync(self.store, client)
        self.assertEqual(client.sent, ["Старая"])
        self.assertTrue(next(c for c in self.store.companies() if c["id"] == company)["crm_id"])

    def test_upsert_does_not_recreate_a_lead_deleted_in_crm(self):
        crm = EspoClient("http://crm.test", api_key="test")
        calls = []
        crm.request = lambda method, path, payload=None: calls.append(method) or {"list": []}
        row = {"id": "abc", "crm_id": "crm-abc", "data": {}}
        with self.assertRaises(LeadRemoved):
            crm.upsert(row)
        self.assertEqual(calls, ["GET"])  # Searched only; nothing was created.

    def test_lead_deleted_in_crm_stays_out(self):
        gone = self.add("Удалённая", "https://gone.ru/")
        self.add("Живая", "https://alive.ru/")
        sync(self.store, Client())

        class DeletedClient(Client):
            def upsert(self, company):
                if company["id"] == gone:
                    raise LeadRemoved(REMOVED_REASON)
                return super().upsert(company)

        for name in ("Удалённая", "Живая"):  # A new collection changes both companies.
            self.store.ingest(normalize({"source": "2gis", "source_id": name, "name": name, "city": "Оренбург",
                                         "address": name + " 1", "phone": "+79990000001"}))
        client = DeletedClient()
        result = sync(self.store, client)
        self.assertEqual((client.sent, result["synced"], result["filtered"], result["errors"]),
                         (["Живая"], 1, 1, []))
        row = next(c for c in self.store.companies() if c["id"] == gone)
        self.assertEqual((row["crm_id"], row["removed_from_crm"], row["error"]), (None, 1, REMOVED_REASON))

        self.store.ingest(normalize({"source": "2gis", "source_id": "Удалённая", "name": "Удалённая",
                                     "city": "Оренбург", "address": "Удалённая 1", "phone": "+79990000002"}))
        client = Client()
        result = sync(self.store, client)
        self.assertEqual((client.sent, result["filtered"], result["pending"]), ([], 1, 0))

if __name__ == "__main__":
    unittest.main()
