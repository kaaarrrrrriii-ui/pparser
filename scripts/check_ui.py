"""Browser smoke check: docker compose exec -T collectors python - < scripts/check_ui.py."""
import os
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    base = os.environ.get("ESPO_URL", "http://localhost:8080").rstrip("/")
    name = "CRM UI test " + uuid.uuid4().hex[:12]
    artifacts = Path(os.environ.get("LEADGEN_DATA", "./var")) / "test-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    lead_id = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(base + "/")
            expect(page.locator("#login")).to_have_count(0, timeout=30000)
            expect(page.locator('a[data-action="create"]')).to_be_visible(timeout=30000)
            expect(page.locator('#navbar .menu-container')).to_have_count(0)
            expect(page.locator('.lead-status-tabs button')).to_have_count(8, timeout=30000)
            in_progress = page.locator('.lead-status-tabs button').filter(has_text="В работе")
            in_progress.click()
            expect(in_progress).to_have_class("btn btn-default active")
            all_statuses = page.locator('.lead-status-tabs button').filter(has_text="Все")
            all_statuses.click()
            expect(all_statuses).to_have_class("btn btn-default active")
            page.screenshot(path=str(artifacts / "crm-leads.png"), full_page=True)
            page.locator('a[data-action="create"]').click()
            page.locator('input[data-name="accountName"]').fill(name)
            expect(page.locator('.field[data-name="assignedUser"]')).to_have_count(0)
            page.locator('input[data-name="website"]').fill("https://example.invalid/")
            note = page.locator('textarea[data-name="description"]')
            note.fill("Заметка из браузерного теста")
            note.press("End")
            note.press("Shift+Enter")
            note.type("Вторая строка")
            with page.expect_response(lambda response: "/api/v1/Lead" in response.url and
                                      response.request.method == "POST") as created:
                note.press("Enter")
            response = created.value
            assert response.ok, (f"UI create returned HTTP {response.status}; "
                                 f"origin={response.request.header_value('origin')}; "
                                 f"site={response.request.header_value('sec-fetch-site')}; "
                                 f"reason={response.header_value('x-status-reason')}")
            lead_id = response.json()["id"]
            saved_note = response.json().get("description", "")
            assert saved_note == "Заметка из браузерного теста\nВторая строка", repr(saved_note)
            expect(page.locator('.field[data-name="accountName"]')).to_contain_text(name)
            expect(page.locator('.field[data-name="description"]')).to_contain_text(
                "Заметка из браузерного тестаВторая строка")
            assert page.locator('[data-action="convert"]').count() == 0, "Convert action is still present"
            assert page.locator('[data-action="duplicate"]').count() == 0, "Duplicate action is still present"
            buttons = page.locator('.lead-status-actions button')
            expect(buttons).to_have_count(7)
            expect(page.locator('.lead-status-actions button[data-status="Send Offer"]')).to_have_text("Отправить оффер")
            expect(page.locator('.lead-status-actions button[data-status="Cold Email"]')).to_have_text("Cold-email")
            expect(page.locator('.lead-status-actions button[data-status="New"]')).to_have_class("btn btn-primary")
            callback = page.locator('.lead-status-actions button[data-status="Recycled"]')
            expect(callback).to_have_text("Перезвонить позже")
            with page.expect_response(lambda r: f"/api/v1/Lead/{lead_id}" in r.url and
                                      r.request.method in ("PUT", "PATCH")) as changed:
                callback.click()
            assert changed.value.ok and changed.value.json()["status"] == "Recycled", changed.value.status
            expect(callback).to_have_class("btn btn-primary")
            expect(page.locator('.field[data-name="status"]')).to_contain_text("Перезвонить позже")
            page.locator('button[data-action="edit"], a[data-action="edit"]').first.click()
            expect(page.locator('.lead-status-actions')).to_be_hidden()
            page.locator('button[data-action="cancelEdit"], a[data-action="cancelEdit"], button[data-action="cancel"]').first.click()
            expect(page.locator('.lead-status-actions')).to_be_visible()
            phone_number = "+7999" + str(uuid.uuid4().int)[:7]
            for phone_type, label in (("Personal", "Личный"), ("Administrator", "Администратор"), ("Office", "Не уточнён")):
                response = context.request.put(base + "/api/v1/Lead/" + lead_id, data={"phoneNumberData": [
                    {"phoneNumber": phone_number, "primary": True, "type": phone_type}]},
                    headers={"Origin": base})
                assert response.ok, f"Phone update returned HTTP {response.status}"
                page.reload()
                expect(page.locator('.field[data-name="phoneNumber"]')).to_contain_text(label, timeout=30000)
                expect(page.locator('.lead-status-actions button[data-status="Recycled"]')).to_have_class("btn btn-primary")
            page.screenshot(path=str(artifacts / "crm-lead.png"), full_page=True)
            page.reload()
            expect(page.locator('.field[data-name="accountName"]')).to_contain_text(name, timeout=30000)
            # A new browser context has no cookies or localStorage from the first session.
            other_context = browser.new_context(viewport={"width": 1440, "height": 1000})
            try:
                other_page = other_context.new_page()
                other_page.goto(base + '/#Lead/view/' + lead_id)
                expect(other_page.locator('.field[data-name="accountName"]')).to_contain_text(name, timeout=30000)
                expect(other_page.locator('.field[data-name="description"]')).to_contain_text(
                    "Заметка из браузерного тестаВторая строка")
                expect(other_page.locator('#login')).to_have_count(0)
            finally:
                other_context.close()
            assert not errors, "Browser JavaScript errors: " + "; ".join(errors)
            print("Browser check passed: no login/profile/assignment, creation, notes, reload, new browser session.")
            print("Screenshots: " + str(artifacts))
        except Exception:
            if page.locator("#login").count() == 0:
                page.screenshot(path=str(artifacts / "crm-failure.png"), full_page=True)
            print("Browser JavaScript errors:", errors)
            raise
        finally:
            if lead_id:
                response = context.request.delete(base + "/api/v1/Lead/" + lead_id)
                assert response.ok, f"UI test cleanup returned HTTP {response.status}"
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
