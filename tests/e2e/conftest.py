"""
E2E conftest — Playwright fixtures.
Requires full stack: docker compose up
"""
import pytest

import os

# When running inside docker-compose.test.yml the env var overrides localhost
BASE_URL = os.environ.get("PLAYWRIGHT_BASE_URL", "http://localhost:5173")
API_URL = os.environ.get("API_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api_url():
    return API_URL


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: End-to-end tests — requires docker compose up",
    )


@pytest.fixture(autouse=True)
def check_stack_running(page, base_url):
    """Skip E2E tests if the frontend is not reachable."""
    try:
        response = page.goto(base_url, timeout=5000)
        if response is None or response.status >= 500:
            pytest.skip(f"Frontend not reachable at {base_url}")
    except Exception:
        pytest.skip(f"Frontend not reachable at {base_url} — run: docker compose up")
