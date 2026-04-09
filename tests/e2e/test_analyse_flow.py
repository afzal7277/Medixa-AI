"""
E2E tests — Drug Interaction Analyse flow.

Requires: docker compose up (frontend on :5173, API on :8000)
Uses pytest-playwright (sync API).

Run:
    pytest tests/e2e/test_analyse_flow.py -v --headed   # with browser window
    pytest tests/e2e/test_analyse_flow.py -v            # headless
"""
import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5173"


@pytest.mark.e2e
class TestPageLoad:
    def test_header_title_visible(self, page: Page):
        page.goto(BASE_URL)
        expect(page.locator("h1")).to_contain_text("Medixa AI")

    def test_subtitle_visible(self, page: Page):
        page.goto(BASE_URL)
        expect(page.get_by_text("Understand your medications")).to_be_visible()

    def test_drug_a_input_present(self, page: Page):
        page.goto(BASE_URL)
        expect(page.get_by_placeholder("e.g. warfarin")).to_be_visible()

    def test_drug_b_input_present(self, page: Page):
        page.goto(BASE_URL)
        expect(page.get_by_placeholder("e.g. aspirin")).to_be_visible()

    def test_analyse_button_present(self, page: Page):
        page.goto(BASE_URL)
        expect(page.get_by_text("Analyse Interaction")).to_be_visible()


@pytest.mark.e2e
class TestAutocomplete:
    def test_typing_shows_suggestions(self, page: Page):
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("war")
        # Autocomplete list should appear
        suggestions = page.locator("ul.suggestions li")
        expect(suggestions.first).to_be_visible(timeout=3000)

    def test_clicking_suggestion_fills_input(self, page: Page):
        page.goto(BASE_URL)
        input_a = page.get_by_placeholder("e.g. warfarin")
        input_a.fill("war")
        suggestions = page.locator("ul.suggestions li")
        expect(suggestions.first).to_be_visible(timeout=3000)
        suggestions.first.click()
        # Input should now have a value
        value = input_a.input_value()
        assert len(value) >= 3

    def test_no_suggestions_for_unknown_drug(self, page: Page):
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("zzzznotadrug")
        suggestions = page.locator("ul.suggestions li")
        expect(suggestions).to_have_count(0)


@pytest.mark.e2e
class TestAnalyseFlow:
    def test_error_shown_when_both_drugs_same(self, page: Page):
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("aspirin")
        page.get_by_placeholder("e.g. aspirin").fill("aspirin")
        page.get_by_text("Analyse Interaction").click()
        expect(page.get_by_text("two different drugs")).to_be_visible(timeout=3000)

    def test_error_shown_when_drug_missing(self, page: Page):
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("warfarin")
        # Leave drug B empty
        page.get_by_text("Analyse Interaction").click()
        expect(page.locator(".error-text")).to_be_visible(timeout=3000)

    def test_full_analyse_flow_with_known_drugs(self, page: Page):
        """Happy-path: warfarin + aspirin → severity badge + explanation."""
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("warfarin")
        page.get_by_placeholder("e.g. aspirin").fill("aspirin")
        page.get_by_text("Analyse Interaction").click()

        # Severity badge must appear (wait up to 30s for ML + GenAI)
        severity_badge = page.locator(".severity-badge")
        expect(severity_badge).to_be_visible(timeout=30000)

        # Explanation section must have text
        explanation = page.locator(".explanation-text")
        expect(explanation).to_be_visible(timeout=30000)
        text = explanation.inner_text()
        assert len(text) > 20

    def test_confidence_meter_visible_after_analyse(self, page: Page):
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("warfarin")
        page.get_by_placeholder("e.g. aspirin").fill("aspirin")
        page.get_by_text("Analyse Interaction").click()

        expect(page.locator(".meter-track")).to_be_visible(timeout=30000)

    def test_sources_displayed_after_analyse(self, page: Page):
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("warfarin")
        page.get_by_placeholder("e.g. aspirin").fill("aspirin")
        page.get_by_text("Analyse Interaction").click()

        # Sources chips (openfda_events etc.)
        expect(page.locator(".source-chip").first).to_be_visible(timeout=30000)

    def test_analyse_button_disabled_during_loading(self, page: Page):
        page.goto(BASE_URL)
        page.get_by_placeholder("e.g. warfarin").fill("warfarin")
        page.get_by_placeholder("e.g. aspirin").fill("aspirin")

        btn = page.get_by_text("Analyse Interaction")
        btn.click()
        # Button should be disabled while loading
        expect(page.get_by_text("Analysing...")).to_be_visible(timeout=5000)


@pytest.mark.e2e
class TestDarkMode:
    def test_dark_mode_toggles(self, page: Page):
        page.goto(BASE_URL)
        toggle = page.locator(".theme-btn")
        expect(toggle).to_be_visible()

        initial_text = toggle.inner_text()
        toggle.click()
        new_text = toggle.inner_text()
        assert initial_text != new_text

    def test_dark_mode_persists_on_reload(self, page: Page):
        page.goto(BASE_URL)
        toggle = page.locator(".theme-btn")
        # Enable dark mode
        if "Dark" in toggle.inner_text():
            toggle.click()
        # Reload
        page.reload()
        expect(page.locator("html.dark")).to_be_visible(timeout=2000)
