"""Queue jobs run the moved pipelines without a window."""

import os
import threading
from dataclasses import replace

from noveldb.core.events import CallbackEvents, CancelToken
from noveldb.core.external.browser_worker import BrowserWorker
from noveldb.core.external.job import ExternalJob, needs_browser_start
from noveldb.core.jobs import JobDispatcher
from noveldb.core.npia.job import NpiaJob
from noveldb.core.queue.model import DONE, Job
from noveldb.core.queue.runner import QueueRunner
from noveldb.core.settings.legacy_vars import Value, external_values, npia_values
from noveldb.core.settings.model import NPIA, AppSettings, SharedOverrides, SharedSettings


def _settings(tmp_path, **shared):
    s = AppSettings(
        shared=replace(SharedSettings(), formats=("txt",), interval_min=0.0, interval_max=0.0, **shared),
        overrides={NPIA: SharedOverrides(threads=1, interval_min=1.5)},
    )
    return replace(s, output=replace(s.output, ask_each_time=False, output_dir=str(tmp_path)))


def test_npia_values_use_the_npia_override(tmp_path):
    values = npia_values(_settings(tmp_path), "123")
    assert values["var_threads"].get() == 1
    assert values["var_interval"].get() == 1.5
    assert values["var_save_txt"].get() is True and values["var_save_epub"].get() is False
    assert values["var_quick_enable"].get() is True
    assert values["var_quick_path"].get() == str(tmp_path)


def test_external_values_use_the_shared_set_and_parent_options(tmp_path):
    values = external_values(_settings(tmp_path, threads=3), "https://example.com/b")
    assert values["_var_ext_threads"].get() == 3
    assert values["_var_format_txt"].get() is True
    assert values["_parent_gui"].var_quick_path.get() == str(tmp_path)
    assert values["_retry_variable"].get() == 5


def test_value_round_trips():
    v = Value(1)
    v.set(2)
    assert v.get() == 2


class FakeScraper:
    _stop_requested = False
    abort_reason = ""
    _context = None

    def __init__(self, logger=None):
        self.log = logger or (lambda text: None)
        self.thread = threading.current_thread().name
        self.started = 0

    def __getattr__(self, name):
        if name.startswith("is_"):
            return lambda url: name == "is_jara"
        raise AttributeError(name)

    def prepare_download_browser(self):
        pass

    def start(self):
        self.started += 1

    def parse_book(self, url):
        self.log("parsing")
        return {
            "bookname": "Fake Book", "author": "Author", "chapterCount": 2, "bookUrl": url, "_jara": True,
            "chapters": [{"name": "One", "url": url + "/1"}, {"name": "Two", "url": url + "/2"}],
        }

    def parse_chapter_batch(self, batch, interval=0.5, _skip_sfc_app=False, success_callback=None, interval_max=None):
        out = []
        for index, chapter in enumerate(batch):
            result = {"chapterName": chapter["name"], "contentText": f"Text of {chapter['name']}",
                      "contentHtml": f"<p>Text of {chapter['name']}</p>"}
            if success_callback:
                success_callback(index, result)
            out.append(result)
        return out


def test_browser_worker_runs_calls_on_its_thread_with_one_scraper():
    logs = []
    worker = BrowserWorker(scraper_factory=FakeScraper)
    try:
        first = worker.submit(lambda s: (s.thread, id(s))).result(5)
        second = worker.submit(lambda s: (s.log("hello"), id(s))[1], log=logs.append).result(5)
        assert first[0] == "browser-worker"
        assert first[1] == second
        assert logs == ["hello"]
    finally:
        worker.shutdown()


def test_external_job_writes_the_book(tmp_path):
    events = []
    job = ExternalJob("https://example.com/book", _settings(tmp_path), events=CallbackEvents(log=events.append))
    worker = BrowserWorker(scraper_factory=FakeScraper)
    try:
        result = worker.submit(job.run, log=job._log).result(30)
    finally:
        worker.shutdown()
    assert result["ok"], (result, events)
    assert result["title"] == "Fake Book"
    assert len(result["outputs"]) == 1 and result["outputs"][0].endswith(".txt")
    text = open(result["outputs"][0], encoding="utf-8").read()
    assert "Text of One" in text and "Text of Two" in text
    assert os.path.dirname(result["outputs"][0]) == str(tmp_path)


def test_generic_sites_start_the_shared_browser_once():
    scraper = FakeScraper()
    scraper.__class__ = type("Generic", (FakeScraper,), {"__getattr__": lambda self, name: (lambda url: False) if name.startswith("is_") else (_ for _ in ()).throw(AttributeError(name))})
    assert needs_browser_start(scraper, "https://example.com/x")
    scraper._context = object()
    assert not needs_browser_start(scraper, "https://example.com/x")


def test_npia_job_reports_a_missing_id(tmp_path):
    class FakeDownloader:
        def __init__(self, auth, log):
            self.stop_signal = False

    warnings = []
    job = NpiaJob("not an id", _settings(tmp_path), auth=None, downloader_factory=FakeDownloader)
    job.prompts.warn = lambda title, message: warnings.append(title)
    result = job.run()
    assert result["ok"] is False
    assert result["error"] == "Missing Novel ID"
    assert warnings == ["Missing Novel ID"]


def test_dispatcher_routes_by_engine(tmp_path):
    calls = []

    class FakeNpia:
        def __init__(self, novel_input, *args):
            calls.append(("npia", novel_input))

        def run(self):
            return {"ok": True, "outputs": ["a.epub"]}

    worker = BrowserWorker(scraper_factory=FakeScraper)
    runner = QueueRunner(JobDispatcher(lambda: _settings(tmp_path), None, worker, lambda job: CallbackEvents(),
                                       npia_job_factory=FakeNpia), max_concurrent=2)
    try:
        npia, ext = runner.submit([Job("123", source="npia", engine="npia_native"),
                                   Job("https://example.com/book", source="jara", engine="browser")])
        assert runner.wait_idle(30)
        assert npia.state == DONE and ext.state == DONE, (npia.error, ext.error)
        assert calls == [("npia", "123")]
        assert ext.outputs and ext.title == "Fake Book"
    finally:
        worker.shutdown()


def test_cancelled_external_job_stops(tmp_path):
    token = CancelToken()
    token.cancel()
    job = ExternalJob("https://example.com/book", _settings(tmp_path), cancel=token)
    job.stop()
    assert job._download_cancelled and not job._downloading
