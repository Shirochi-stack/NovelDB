"""Optional clocks, concurrent chapters, portable config and actual pipeline logs."""
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from noveldb.core.chapter_timing import _BROWSER_TIMER, timed_chapter
from noveldb.core.settings.desktop_config import import_config, export_config
from noveldb.core.settings.model import AppSettings
from noveldb.core.settings.legacy_vars import npia_values, external_values


def test_timing_is_optional_and_portable():
    assert not import_config({}).shared.chapter_timing
    settings = import_config({"ext_chapter_timing": True})
    assert settings.shared.chapter_timing
    assert export_config(settings)["ext_chapter_timing"] is True
    assert npia_values(settings, "123")["var_chapter_timing"].get()
    assert external_values(settings, "https://example.com/book")["_var_chapter_timing"].get()


def test_worker_clocks_are_independent_and_nested_measurements_survive(monkeypatch):
    from noveldb.core import chapter_timing
    import threading
    local = threading.local()
    monkeypatch.setattr(chapter_timing, "perf_counter", lambda: next(local.clock))

    class Worker:
        chapter_timing = True
        @timed_chapter
        def parse(self, seconds):
            return {"chapterName": str(seconds)}
        @timed_chapter
        def nested(self):
            return {"_scrape_seconds": 1.25}

    def work(seconds):
        local.clock = iter([100.0, 100.0 + seconds])
        return Worker().parse(seconds)["_scrape_seconds"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(work, [2.0, 7.0, 3.0])) == [2.0, 7.0, 3.0]
    local.clock = iter([0, 99])
    assert Worker().nested()["_scrape_seconds"] == 1.25
    worker = Worker()
    worker.chapter_timing = False
    assert worker.parse(5) == {"chapterName": "5"}


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed to exercise the browser function")
def test_browser_batch_times_each_concurrent_chapter_and_disables_cleanly():
    # Deterministic per-task JS clock. Resolve the second chapter first to catch shared clocks.
    script = r"""
    global.window = global;
    let clock = 0;
    global.performance = {now: () => clock};
    const resolve = {};
    window.read = name => new Promise(done => { resolve[name] = done; });
    const install = INSTALL;
    (async () => {
      install(['read', true]);
      const first = read('first');
      clock = 1000;
      const second = read('second');
      clock = 4000;
      resolve.second(JSON.stringify({chapterName: 'second'}));
      const b = JSON.parse(await second);
      clock = 8000;
      resolve.first(JSON.stringify({chapterName: 'first'}));
      const a = JSON.parse(await first);
      install(['read', true]); // Installation must be idempotent.
      install(['read', false]);
      const plain = read('plain');
      resolve.plain(JSON.stringify({chapterName: 'plain'}));
      console.log(JSON.stringify([a,b,JSON.parse(await plain)]));
    })();
    """.replace("INSTALL", _BROWSER_TIMER)
    result = subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [
        {"chapterName": "first", "_scrape_seconds": 8},
        {"chapterName": "second", "_scrape_seconds": 3},
        {"chapterName": "plain"},
    ]


@pytest.mark.parametrize("enabled", [False, True])
def test_external_pipeline_logs_one_duration_per_completed_chapter(tmp_path, enabled):
    from test_core_jobs import FakeScraper, _settings
    from noveldb.core.events import CallbackEvents
    from noveldb.core.external.job import ExternalJob

    class Scraper(FakeScraper):
        def parse_chapter_batch(self, batch, **options):
            # Emulate a non-streaming engine. The pipeline must still log completed chapters.
            return [dict(chapterName=c["name"], contentText="Example chapter text", contentHtml="<p>Text</p>",
                         _scrape_seconds=1.25 + i) for i,c in enumerate(batch)]

    settings = _settings(tmp_path, use_cache=False, chapter_timing=enabled)
    logs = []
    scraper = Scraper()
    result = ExternalJob("https://example.com/book", settings, events=CallbackEvents(log=logs.append)).run(scraper)
    assert result["ok"], result
    assert scraper.chapter_timing is False, "Shared scraper must not retain this job's debug setting"
    assert sum("One (1.25 s)" in line for line in logs) == int(enabled)
    assert sum("Two (2.25 s)" in line for line in logs) == int(enabled)


@pytest.mark.parametrize("enabled", [False, True])
def test_native_npia_job_times_each_completed_chapter(tmp_path, monkeypatch, enabled):
    from noveldb.core.npia.job import NpiaJob
    from noveldb.core.events import CallbackEvents
    from noveldb.core.npia import pipeline

    class Downloader:
        stop_signal = False
        def __init__(self, auth, log): pass
        def fetch_metadata(self, novel_id): return {"title": "Timing test", "author": "Test"}
        def fetch_notice_ids(self, novel_id): return []
        def fetch_chapter_list(self, novel_id, **kwargs):
            return [{"id": str(i), "title": f"Chapter {i}"} for i in (1,2)]
        def download_chapter_content(self, chapter_id, max_retries): return "chapter data"

    monkeypatch.setattr(pipeline, "extract_chapter_content_and_images",
                        lambda *args, **kwargs: ("<p>Chapter text</p>", [], 0))
    settings = AppSettings()
    settings = replace(settings, shared=replace(settings.shared, chapter_timing=enabled, use_cache=False,
                                                formats=("txt",), interval_min=0, interval_max=0),
                       npia=replace(settings.npia, include_notices=False))
    logs = []
    job = NpiaJob("123", settings, SimpleNamespace(session=None), events=CallbackEvents(log=logs.append),
                  output_dir=str(tmp_path), downloader_factory=Downloader)
    result = job.run()
    assert result["ok"], result
    completed = [line for line in logs if "Downloaded: Chapter" in line]
    assert len(completed) == 2
    import re
    assert all(bool(re.search(r"\(\d+\.\d{2} s\)$", line)) == enabled for line in completed)
