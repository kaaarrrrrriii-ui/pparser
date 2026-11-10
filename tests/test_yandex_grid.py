"""Exercise collection/checkpoint logic with a fake browser, without external requests."""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit


class GridTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        if importlib.util.find_spec("httpx") is None:
            self.stack.enter_context(patch.dict(sys.modules, {"httpx": types.ModuleType("httpx")}))
        spec = importlib.util.spec_from_file_location("test_yamaps", Path(__file__).resolve().parents[1] / "vendor/yamaps_parser/yamaps_parser.py")
        self.module = importlib.util.module_from_spec(spec)
        self.stack.enter_context(patch.dict(sys.modules, {"test_yamaps": self.module}))
        spec.loader.exec_module(self.module)
        browser_api = MagicMock()
        self.stack.enter_context(patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                                         "playwright.sync_api": browser_api}))
        self.stack.enter_context(patch.object(self.module.time, "sleep", lambda _: None))
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.checkpoint = Path(self.tmp) / "checkpoint.json"

    def org(self, item_id):
        return self.module.parse_org({"id": item_id, "title": "Клиника"})

    def test_parent_results_survive_failed_children_and_resume_skips_finished_cells(self):
        calls = []

        def scrape(page, url, **kwargs):
            q = parse_qs(urlsplit(url).query)
            span = float(q["spn"][0].split(",")[0])
            calls.append(q["ll"][0])
            if span > .03:
                return [self.org("parent-1"), self.org("parent-2")]
            if len(calls) == 2:
                raise TimeoutError("child failed")
            return [self.org("child-" + q["ll"][0])]

        with patch.object(self.module, "_scrape_page", scrape):
            with self.assertRaises(RuntimeError):
                self.module.search_grid("https://yandex.ru/maps/?text=test", (0, 0, .04, .04),
                                        cell=(.04, .04), split_threshold=2, retries=1,
                                        checkpoint=str(self.checkpoint), verbose=False)
        saved = json.loads(self.checkpoint.read_text())
        self.assertIn("parent-1", {r["id"] for r in saved["organizations"]})
        self.assertEqual(len(saved["completed"]), 3)
        resumed_calls = []

        def resume(page, url, **kwargs):
            resumed_calls.append(url)
            q = parse_qs(urlsplit(url).query)
            if float(q["spn"][0].split(",")[0]) > .03:
                return [self.org("parent-1"), self.org("parent-2")]
            return [self.org("recovered")]

        with patch.object(self.module, "_scrape_page", resume):
            result = self.module.search_grid("https://yandex.ru/maps/?text=test", (0, 0, .04, .04),
                                             cell=(.04, .04), split_threshold=2, retries=1,
                                             checkpoint=str(self.checkpoint), verbose=False)
        self.assertEqual(len(resumed_calls), 2)
        self.assertIn("recovered", {r.id for r in result})

    def test_captcha_saves_progress_and_stops_without_retries(self):
        calls = []

        def blocked(page, url, **kwargs):
            calls.append(url)
            kwargs["on_organization"](self.org("saved-before-captcha"))
            raise self.module.CollectionBlocked("CAPTCHA")

        with patch.object(self.module, "_scrape_page", blocked):
            with self.assertRaises(self.module.CollectionBlocked):
                self.module.search_grid("https://yandex.ru/maps/", (0, 0, .02, .02), retries=3,
                                        checkpoint=str(self.checkpoint), verbose=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(self.checkpoint.read_text())["organizations"][0]["id"], "saved-before-captcha")

    def test_zero_cell_and_inverted_bbox_fail_before_launching_browser(self):
        with self.assertRaises(ValueError):
            self.module.search_grid("https://yandex.ru/maps/", (0, 0, 1, 1), cell=(0, .02))
        with self.assertRaises(ValueError):
            self.module.search_grid("https://yandex.ru/maps/", (1, 1, 0, 0))


if __name__ == "__main__":
    unittest.main()
