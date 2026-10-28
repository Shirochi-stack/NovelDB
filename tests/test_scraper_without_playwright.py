"""Lite and TurboLite builds have no Playwright: the scraper must still import and run HTTP sources."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, sys
sys.modules["playwright"] = None
sys.modules["playwright.sync_api"] = None
import external_scraper as es
result = {"available": es.PLAYWRIGHT_AVAILABLE}
try:
    es.sync_playwright()
except es.BrowserUnavailableError as exc:
    result["error"] = str(exc)
try:
    raise es.PlaywrightTimeoutError("t")
except es.PlaywrightTimeoutError:
    result["timeout_class"] = True
scraper = es.ExternalScraper(logger=lambda message: None)
result["scraper"] = type(scraper).__name__
print(json.dumps(result))
"""


def _run(env_extra=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **(env_extra or {}))
    proc = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_scraper_imports_and_explains_when_playwright_is_missing():
    result = _run()
    assert result["available"] is False
    assert "Full build" in result["error"]
    assert result["timeout_class"] is True
    assert result["scraper"] == "ExternalScraper"


def test_env_switch_disables_playwright():
    probe = "import external_scraper as es; print(es.PLAYWRIGHT_AVAILABLE)"
    env = dict(os.environ, NOVELDB_DISABLE_PLAYWRIGHT="1")
    proc = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "False"
