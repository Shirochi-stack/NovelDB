"""Queue runner limits, cancel/retry and persistence; events and source detection."""

import threading
import time

import pytest

from noveldb.core import sources
from noveldb.core.events import (
    SAVED_PREFIX,
    CallbackEvents,
    CancelToken,
    LogPump,
    MsgQueueAdapter,
    ProgressTracker,
    format_eta,
)
from noveldb.core.queue.model import CANCELLED, DONE, FAILED, INTERRUPTED, QUEUED, RUNNING, Job
from noveldb.core.queue.runner import JobCancelled, QueueRunner


# -- events -----------------------------------------------------------------------

def test_format_eta_matches_the_old_label():
    assert format_eta(0, 0, None) == ""
    assert format_eta(12, 45, None) == "12/45 (26%)"
    assert format_eta(10, 20, started=100.0, now=110.0) == "10/20 (50%) \u2014 ETA: 10s"
    assert format_eta(10, 100, started=0.0, now=100.0).endswith("ETA: 15m 0s")
    assert format_eta(5, 5, started=1.0, now=2.0).endswith("\u2014 Done")


def test_progress_tracker_is_thread_safe():
    tracker = ProgressTracker()
    tracker.reset(total=4000)
    threads = [threading.Thread(target=lambda: [tracker.advance() for _ in range(1000)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert (tracker.value, tracker.total, tracker.percent) == (4000, 4000, 100)


def test_log_pump_drains_in_batches():
    pump = LogPump()
    for i in range(250):
        pump.push(f"line {i}")
    first = pump.drain(max_items=100, budget=10)
    assert len(first) == 100 and first[0] == "line 0"
    assert pump.backlog
    assert len(pump.drain(max_items=1000, budget=10)) == 150
    assert not pump.backlog


def test_msg_queue_adapter_translates_dialog_messages():
    seen = []
    events = CallbackEvents(
        log=lambda t: seen.append(("log", t)),
        saved=lambda p: seen.append(("saved", p)),
        progress=lambda v, t, i: seen.append(("progress", v, t)),
        book_info=lambda d: seen.append(("book", d["bookname"])),
    )
    adapter = MsgQueueAdapter(events)
    adapter.put(("log", f"{SAVED_PREFIX}C:/books/a.epub"))
    adapter.put(("progress", (3, 10)))
    adapter.put(("book_parsed", {"bookname": "Title"}))
    assert ("saved", "C:/books/a.epub") in seen
    assert ("progress", 3, 10) in seen
    assert ("book", "Title") in seen


def test_cancel_token_wakes_waiters():
    token = CancelToken()
    threading.Timer(0.05, token.cancel).start()
    assert token.wait(5) is True
    assert token.cancelled


# -- sources ----------------------------------------------------------------------

def test_detect_source_for_ids_and_batch_lines():
    assert sources.detect_source("123456") == "npia"
    assert sources.detect_source("Some Title, 123456") == "npia"
    assert sources.detect_source("not a url") is None
    assert sources.detect_source("") is None


def test_unknown_sites_use_the_generic_reader():
    assert sources.detect_source("https://example.com/book/1") == "reader"


def test_engine_for_npia_lines():
    assert sources.engine_for("123456") == sources.NPIA_NATIVE
    assert sources.engine_for("123456", npia_engine="browser") == sources.BROWSER
    assert sources.engine_for("https://example.com/book/1") == sources.BROWSER


def test_split_inputs_skips_blanks_comments_and_duplicates():
    assert sources.split_inputs("a\n\n# note\nb\na\n  c  ") == ["a", "b", "c"]


# -- queue ------------------------------------------------------------------------

def _runner(executor, **kwargs):
    return QueueRunner(executor, **kwargs)


def test_runs_jobs_and_records_results():
    runner = _runner(lambda job, token: {"ok": True, "outputs": [f"{job.input}.epub"], "title": job.input.upper()})
    jobs = runner.submit([Job("a"), Job("b")])
    assert runner.wait_idle(5)
    assert [j.state for j in jobs] == [DONE, DONE]
    assert jobs[0].outputs == ["a.epub"] and jobs[0].title == "A"


def test_engine_limits_and_max_concurrent():
    gate = threading.Event()
    peak = {"all": 0, "npia_native": 0}
    current = {"all": 0, "npia_native": 0, "browser": 0}
    lock = threading.Lock()

    def executor(job, token):
        with lock:
            current["all"] += 1
            current[job.engine] += 1
            peak["all"] = max(peak["all"], current["all"])
            peak["npia_native"] = max(peak["npia_native"], current["npia_native"])
        gate.wait(5)
        with lock:
            current["all"] -= 1
            current[job.engine] -= 1
        return {}

    runner = _runner(executor, max_concurrent=3)
    runner.submit([Job("n1", engine="npia_native"), Job("n2", engine="npia_native"), Job("b1", engine="browser")])
    time.sleep(0.2)
    assert {j.input for j in runner.running()} == {"n1", "b1"}
    gate.set()
    assert runner.wait_idle(5)
    assert peak == {"all": 2, "npia_native": 1}


def test_cancel_queued_and_running_jobs():
    started = threading.Event()

    def executor(job, token):
        started.set()
        if token.wait(5):
            raise JobCancelled()
        return {}

    runner = _runner(executor)
    first, second = runner.submit([Job("a"), Job("b")])
    assert started.wait(5)
    runner.cancel(second.id)
    runner.cancel(first.id)
    assert runner.wait_idle(5)
    assert (first.state, second.state) == (CANCELLED, CANCELLED)


def test_failures_keep_the_queue_going_and_can_be_retried():
    calls = []

    def executor(job, token):
        calls.append(job.input)
        if job.input == "bad" and calls.count("bad") == 1:
            raise RuntimeError("boom")
        return {}

    runner = _runner(executor)
    bad, good = runner.submit([Job("bad"), Job("good")])
    assert runner.wait_idle(5)
    assert bad.state == FAILED and "boom" in bad.error
    assert good.state == DONE
    runner.retry(bad.id)
    assert runner.wait_idle(5)
    assert bad.state == DONE


def test_persistence_restores_paused_and_interrupted(tmp_path):
    path = tmp_path / "queue.json"
    gate = threading.Event()
    runner = _runner(lambda job, token: gate.wait(5) and {}, persist_path=path)
    running, waiting = runner.submit([Job("a"), Job("b")])
    time.sleep(0.2)
    assert running.state == RUNNING
    restored = _runner(lambda job, token: {}, persist_path=path)
    states = {job.input: job.state for job in restored.jobs}
    assert states == {"a": INTERRUPTED, "b": QUEUED}
    assert restored.paused
    restored.resume()
    assert restored.wait_idle(5)
    assert restored.job(waiting.id).state == DONE
    gate.set()
    runner.wait_idle(5)


def test_progress_updates_reach_listeners():
    changes = []
    runner = None

    def executor(job, token):
        runner.update(job, value=1, total=2, info="1/2")
        return {}

    runner = _runner(executor, on_change=lambda job: changes.append((job.state, job.value)))
    runner.submit([Job("a")])
    assert runner.wait_idle(5)
    assert (RUNNING, 1) in changes and changes[-1][0] == DONE
