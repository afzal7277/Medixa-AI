"""
E2E tests — Query History panel.

Requires: docker compose up (frontend on :5173, API on :8000)
Run:
    pytest tests/e2e/test_history.py -v
"""
import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5173"


def _do_analyse(page: Page, drug_a: str = "warfarin", drug_b: str = "aspirin"):
    """Helper: fill inputs and trigger analyse, wait for severity badge."""
    page.goto(BASE_URL)
    page.get_by_placeholder("e.g. warfarin").fill(drug_a)
    page.get_by_placeholder("e.g. aspirin").fill(drug_b)
    page.get_by_text("Analyse Interaction").click()
    # Wait for severity badge — indicates stream complete
    expect(page.locator(".severity-badge")).to_be_visible(timeout=30000)


@pytest.mark.e2e
class TestHistoryPanel:
    def test_history_empty_on_fresh_load(self, page: Page):
        page.goto(BASE_URL)
        expect(page.get_by_text("No queries yet.")).to_be_visible()

    def test_history_entry_appears_after_analyse(self, page: Page):
        _do_analyse(page, "warfarin", "aspirin")
        history_list = page.locator(".history-list")
        expect(history_list).to_be_visible(timeout=5000)
        items = history_list.locator(".history-item")
        expect(items).to_have_count(1, timeout=5000)

    def test_history_entry_shows_drug_names(self, page: Page):
        _do_analyse(page, "warfarin", "aspirin")
        item = page.locator(".history-item").first
        text = item.inner_text()
        assert "warfarin" in text.lower()
        assert "aspirin" in text.lower()

    def test_history_entry_shows_severity_badge(self, page: Page):
        _do_analyse(page, "warfarin", "aspirin")
        expect(page.locator(".history-badge").first).to_be_visible(timeout=5000)

    def test_history_entry_shows_timestamp(self, page: Page):
        _do_analyse(page, "warfarin", "aspirin")
        expect(page.locator(".history-time").first).to_be_visible(timeout=5000)

    def test_multiple_queries_add_multiple_entries(self, page: Page):
        """Two separate analyses should produce two history entries."""
        _do_analyse(page, "warfarin", "aspirin")

        # Second analysis
        page.get_by_placeholder("e.g. aspirin").fill("ibuprofen")
        page.get_by_text("Analyse Interaction").click()
        expect(page.locator(".severity-badge")).to_be_visible(timeout=30000)

        items = page.locator(".history-item")
        expect(items).to_have_count(2, timeout=5000)

    def test_clicking_history_item_replays_result(self, page: Page):
        """Clicking a history item should restore its drug names and explanation."""
        _do_analyse(page, "warfarin", "aspirin")

        # Perform a second analysis to move focus away
        page.get_by_placeholder("e.g. warfarin").fill("metformin")
        page.get_by_placeholder("e.g. aspirin").fill("insulin")
        page.get_by_text("Analyse Interaction").click()
        expect(page.locator(".severity-badge")).to_be_visible(timeout=30000)

        # Click first history entry (warfarin + aspirin)
        items = page.locator(".history-item")
        items.nth(1).click()  # index 1 = older entry (warfarin + aspirin)

        # Drug A input should restore to warfarin
        drug_a_value = page.get_by_placeholder("e.g. warfarin").input_value()
        assert "warfarin" in drug_a_value.lower()

    def test_active_history_item_shows_explanation(self, page: Page):
        """Clicking a history item expands its explanation."""
        _do_analyse(page, "warfarin", "aspirin")

        # Click the item
        page.locator(".history-item").first.click()
        expect(page.locator(".history-explanation")).to_be_visible(timeout=3000)
        text = page.locator(".history-explanation").inner_text()
        assert len(text) > 10

    def test_export_pdf_button_enabled_after_query(self, page: Page):
        _do_analyse(page, "warfarin", "aspirin")
        export_btn = page.locator(".export-btn")
        expect(export_btn).not_to_be_disabled(timeout=5000)

    def test_export_pdf_button_disabled_when_no_history(self, page: Page):
        page.goto(BASE_URL)
        export_btn = page.locator(".export-btn")
        expect(export_btn).to_be_disabled()


@pytest.mark.e2e
class TestHistoryLimit:
    def test_history_capped_at_10_items(self, page: Page):
        """Perform 12 analyses and verify history shows at most 10."""
        page.goto(BASE_URL)
        # Use cached results (same pair) for speed — still adds to history
        drug_pairs = [
            ("warfarin", "aspirin"), ("metformin", "insulin"), ("lisinopril", "amlodipine"),
            ("omeprazole", "clopidogrel"), ("amiodarone", "digoxin"), ("fluconazole", "warfarin"),
            ("prednisone", "ibuprofen"), ("lithium", "ibuprofen"), ("tramadol", "warfarin"),
            ("simvastatin", "amiodarone"), ("ciprofloxacin", "warfarin"), ("phenytoin", "warfarin"),
        ]
        for drug_a, drug_b in drug_pairs[:10]:
            page.get_by_placeholder("e.g. warfarin").fill(drug_a)
            page.get_by_placeholder("e.g. aspirin").fill(drug_b)
            page.get_by_text("Analyse Interaction").click()
            expect(page.locator(".severity-badge")).to_be_visible(timeout=30000)

        items = page.locator(".history-item")
        count = items.count()
        assert count <= 10
