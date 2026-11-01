"""Image saving details of the external-source pipeline: saved pages, mirrors, Stop while building."""

from noveldb.core.external.pipeline import ExternalPipelineMixin, _OutputStopped


class Saver(ExternalPipelineMixin):
    def __init__(self):
        self._download_cancelled = False
        self.tried = []
        self.dead = set()

    def _download_image_python(self, url, label="Image", **_options):
        self.tried.append(url)
        return None if url.split("/")[2] in self.dead else b"x" * 200


def test_a_saved_page_file_is_read_and_a_missing_one_is_none(tmp_path):
    page = tmp_path / "page"
    page.write_bytes(b"RIFF....WEBP")
    assert ExternalPipelineMixin._decode_image_data_url(f"file:{page}") == b"RIFF....WEBP"
    assert ExternalPipelineMixin._decode_image_data_url(f"file:{tmp_path / 'gone'}") is None


def test_a_mirror_that_failed_is_tried_last_next_time():
    saver = Saver()
    saver.dead = {"dead.test"}
    urls = ["https://dead.test/a.js", "https://live.test/a.js"]
    assert saver._download_image_mirrors(urls, "page 1")
    assert saver.tried == urls
    saver.tried.clear()
    assert saver._download_image_mirrors(["https://dead.test/b.js", "https://live.test/b.js"], "page 2")
    assert saver.tried == ["https://live.test/b.js"]


def test_every_mirror_failing_gives_none_after_two_rounds():
    saver = Saver()
    saver.dead = {"a.test", "b.test"}
    assert saver._download_image_mirrors(["https://a.test/1", "https://b.test/1"], "page") is None
    assert len(saver.tried) == 4


def test_stop_counts_only_when_pressed_while_the_file_is_built():
    saver = Saver()
    assert not saver._generation_stopped()  # outside a build
    saver._generation_stop_baseline = False  # a build started with no Stop
    assert not saver._generation_stopped()
    saver._download_cancelled = True  # Stop pressed during the build
    assert saver._generation_stopped()
    saver._generation_stop_baseline = True  # a build that Stop with "generate on stop" started
    assert not saver._generation_stopped()
    assert issubclass(_OutputStopped, BaseException) and not issubclass(_OutputStopped, Exception)
