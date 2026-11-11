import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from noveldb.core.external.pipeline import ExternalPipelineMixin
from tests.test_external_dialog_mpia import Setting, make_download_dialog


def test_killed_download_reuses_completed_chapter_and_image(tmp_path):
    path = tmp_path / "book.json"
    ready = tmp_path / "ready"
    worker = """
import sys, time
from pathlib import Path
from types import SimpleNamespace
from noveldb.core.external.pipeline import ExternalPipelineMixin
from tests.test_external_dialog_mpia import Setting, make_download_dialog

def fetch(batch, *, success_callback, **options):
    success_callback(1, {"chapterName": "Two", "contentHtml": "<p>Saved</p>", "images": [
        {"url": "https://test.invalid/image", "data": "data:image/png;base64,AA=="}
    ]})
    Path(sys.argv[2]).touch()
    time.sleep(120)

job = make_download_dialog(SimpleNamespace(_context=True, parse_chapter_batch=fetch))
job._var_use_cache = Setting(True)
job._var_cache_images = Setting(True)
job._external_cache_path = lambda: sys.argv[1]
ExternalPipelineMixin._do_download(job, [{"id": "1", "name": "One"}, {"id": "2", "name": "Two"}],
                                  0, 2, 0, num_threads=2)
"""
    with subprocess.Popen([sys.executable, "-c", worker, str(path), str(ready)],
                          cwd=Path(__file__).resolve().parents[1]) as process:
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), "Child did not checkpoint the second chapter"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    fetched = []

    def fetch(batch, *, success_callback, **options):
        fetched.extend(chapter["id"] for chapter in batch)
        result = {"chapterName": "One"}
        success_callback(0, result)
        return [result]

    job = make_download_dialog(SimpleNamespace(_context=True, parse_chapter_batch=fetch))
    job._var_use_cache = Setting(True)
    job._var_cache_images = Setting(True)
    job._external_cache_path = lambda: str(path)
    ExternalPipelineMixin._do_download(job, [{"id": "1", "name": "One"}, {"id": "2", "name": "Two"}],
                                      0, 2, 0, num_threads=2)
    assert fetched == ["1"]
    assert job._chapter_results[1]["contentHtml"] == "<p>Saved</p>"
    assert job._chapter_results[1]["images"][0]["data"] == "data:image/png;base64,AA=="
