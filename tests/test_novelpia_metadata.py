from types import SimpleNamespace

import pytest

from downloader_core import DownloaderCore, parse_novelpia_status


def novel_page(badge):
    # The donation message on novel 433986 contains 완결 even while ongoing.
    return f"""
    <nav><a>완결</a></nav>
    <script>const label = '완결';</script>
    <div class="epnew-novel-info">
      <p class="in-badge"><span class="b_plus s_inv">PLUS</span>{badge}</p>
      <div class="synopsis">완결까지 달립니다.</div>
    </div>
    <option>작가님의 건강을 기원하며 완결까지 파이팅하시길 바랍니다.</option>
    <aside><p class="in-badge"><span class="b_comp">완결</span></p></aside>
    """


@pytest.mark.parametrize('badge, expected', [
    ('', 'Ongoing'),
    ('<span class="b_comp s_inv">완결</span>', 'Completed'),
])
def test_core_metadata_uses_only_the_novel_completion_badge(badge, expected):
    core = DownloaderCore(None, lambda _message: None)
    core._request_with_retries = lambda *_args, **_kwargs: SimpleNamespace(
        text=novel_page(badge),
    )

    assert core.fetch_metadata('433986')['status'] == expected


@pytest.mark.parametrize('source', [
    '',
    '<h1>Login required</h1><a>완결</a>',
    '<p class="in-badge"><span class="b_comp">완결</span></p>',
])
def test_missing_novel_metadata_does_not_invent_a_status(source):
    assert parse_novelpia_status(source) == ''
