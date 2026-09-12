import queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper


class Setting:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def make_download_dialog(scraper, book_data=None):
    return SimpleNamespace(
        _scraper=scraper,
        _book_data=book_data or {'_munpia': True},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=Mock(return_value=True),
        _log=Mock(),
    )


def test_munpia_catalog_uses_normalized_dialog_rate_limits():
    scraper = SimpleNamespace()
    dialog = SimpleNamespace(
        _scraper=scraper,
        _parent_gui=SimpleNamespace(),
        _var_interval=Setting(1.5),
        _var_interval_max=Setting(0.3),
        _var_kakao_skip_last_page=Setting(False),
        _var_kakao_keep_filler=Setting(False),
        _var_ntk_novelpia_cover=Setting(False),
        _var_syosetu_amazon_cover=Setting(False),
    )
    dialog._get_interval_range = lambda: ExternalNovelDialog._get_interval_range(
        dialog,
    )

    ExternalNovelDialog._apply_scraper_options(dialog)

    assert (scraper.munpia_interval, scraper.munpia_interval_max) == (0.3, 1.5)


def test_munpia_download_preserves_ranges_threads_and_rate_limits(monkeypatch):
    batches = []
    scraper = SimpleNamespace(_context=None, start=Mock())

    def fetch(chapters, interval=0, interval_max=None, success_callback=None):
        batches.append((chapters, interval, interval_max))
        results = [{'chapterName': chapter['name']} for chapter in chapters]
        for index, result in enumerate(results):
            success_callback(index, result)
        return results

    scraper.parse_chapter_batch = fetch
    dialog = make_download_dialog(scraper)
    monkeypatch.setattr('external_scraper.random.uniform', lambda low, high: high)
    chapters = [{'name': f'Chapter {number}'} for number in range(1, 7)]

    ExternalNovelDialog._do_download(
        dialog, chapters, 1, 5, 0.3, num_threads=3, interval_max=0.8,
    )

    assert batches == [(chapters[1:4], 0.3, 0.8), (chapters[4:5], 0.3, 0.8)]
    assert [result['_chapter_number'] for result in dialog._chapter_results] == [
        2, 3, 4, 5,
    ]
    dialog._sleep_while_downloading.assert_called_once_with(0.8)
    scraper.start.assert_not_called()


@pytest.mark.parametrize('skip_paid', [False, True])
@pytest.mark.parametrize('site', ['_munpia', '_kakaopage'])
def test_munpia_free_only_excludes_purchases_without_changing_kakao(site, skip_paid):
    fetched = []
    chapters = [
        {'name': 'Free', 'isVIP': False, 'isAccessible': True},
        {'name': 'Purchased', 'isVIP': True, 'isAccessible': True},
        {'name': 'Locked', 'isVIP': True, 'isAccessible': False},
    ]

    def fetch(batch, interval=0, success_callback=None):
        results = []
        for index, chapter in enumerate(batch):
            fetched.append(chapter['name'])
            result = {'chapterName': chapter['name']}
            if chapter['name'] == 'Locked':
                result['_locked'] = True
            elif success_callback:
                success_callback(index, result)
            results.append(result)
        return results

    dialog = make_download_dialog(
        SimpleNamespace(_context=True, parse_chapter_batch=fetch), {site: True},
    )

    ExternalNovelDialog._do_download(
        dialog, chapters, 0, 3, 0, num_threads=3, skip_paid=skip_paid,
    )

    if not skip_paid and site == '_kakaopage':
        assert fetched == ['Free', 'Purchased', 'Locked']
    elif skip_paid and site == '_munpia':
        assert fetched == ['Free']
    else:
        assert fetched == ['Free', 'Purchased']


def test_munpia_unavailable_chapters_do_not_fetch_or_add_batch_delays():
    scraper = SimpleNamespace(_context=True, parse_chapter_batch=Mock())
    dialog = make_download_dialog(scraper)
    chapters = [
        {'name': 'Locked first', 'isVIP': True, 'isAccessible': False},
        {'name': 'Unknown access'},
        {'name': 'Locked last', 'isVIP': True, 'isAccessible': False},
    ]

    def fetch(batch, **options):
        result = {'chapterName': batch[0]['name']}
        options['success_callback'](0, result)
        return [result]

    scraper.parse_chapter_batch.side_effect = fetch

    ExternalNovelDialog._do_download(
        dialog, chapters, 0, 3, 1, num_threads=1,
        skip_paid=False, interval_max=2,
    )

    assert scraper.parse_chapter_batch.call_count == 1
    assert scraper.parse_chapter_batch.call_args.args == ([chapters[1]],)
    assert [result.get('_locked', False) for result in dialog._chapter_results] == [
        True, False, True,
    ]
    assert dialog._chapter_results[1]['_chapter_number'] == 2
    dialog._sleep_while_downloading.assert_not_called()
    assert any(
        '2 chapter(s) unavailable to this account' in call.args[0]
        for call in dialog._log.call_args_list
    )


@pytest.mark.parametrize('entrypoint', ['fetch', 'fetch_and_download', 'batch'])
def test_munpia_metadata_uses_native_browser_startup(monkeypatch, entrypoint):
    instances = []

    class Scraper(ExternalScraper):
        def __init__(self, logger):
            super().__init__(logger=logger)
            self.start = Mock()
            self.parse_book = Mock(return_value={
                '_munpia': True,
                'bookname': 'Example',
                'chapters': [{'name': 'One'}],
                'chapterCount': 1,
            })
            instances.append(self)

    monkeypatch.setattr('external_dialog.ExternalScraper', Scraper)
    dialog = make_download_dialog(None)
    dialog._get_output_dir = lambda: '.'
    dialog._var_from_enabled = Setting(False)
    dialog._var_to_enabled = Setting(False)
    dialog._format_interval_range = lambda low, high: f'{low}-{high}'
    dialog._do_download = Mock()
    dialog.after = Mock()
    url = 'https://novel.munpia.com/564583'

    if entrypoint == 'fetch':
        ExternalNovelDialog._do_fetch(dialog, url)
    elif entrypoint == 'fetch_and_download':
        ExternalNovelDialog._do_fetch_and_download(dialog, url, 0, 2, False)
    else:
        ExternalNovelDialog._do_batch(dialog, ([url], 0, 2, False, 1))

    instances[0].start.assert_not_called()
    instances[0].parse_book.assert_called_once_with(url)
    messages = list(dialog._msg_queue.queue)
    assert any(kind == 'book_parsed' for kind, _ in messages)
    assert not any(kind == 'error' for kind, _ in messages)


def test_munpia_login_uses_saved_regular_browser_profile():
    url = 'https://novel.munpia.com/564583'
    dialog = SimpleNamespace(
        _normalised_url=lambda: url,
        _url_var=Setting(''),
        _save_ext_config=lambda: None,
        _var_regular_browser=Setting(False),
        _append_log=Mock(),
        _work_queue=queue.Queue(),
    )
    for name in (
        '_btn_download', '_btn_browser', '_btn_sfacg_app',
        '_chk_regular_browser', '_btn_paste_batch', '_btn_batch_file',
    ):
        setattr(dialog, name, Mock())

    ExternalNovelDialog._on_enter_browser(dialog)

    assert dialog._var_regular_browser.get() is True
    assert dialog._work_queue.get_nowait() == (
        'browser', {'url': url, 'regular': True},
    )


@pytest.mark.parametrize('output_format', ['epub', 'pdf', 'cbz'])
def test_munpia_output_images_reuse_scoped_browser_cookies(
    monkeypatch, tmp_path, output_format,
):
    import base64
    import zipfile

    import requests

    from downloader_core import DownloaderCore

    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8'
        '/x8AAwMCAO+aLl8AAAAASUVORK5CYII='
    )
    book_url = 'https://www.munpia.com/novel/detail/564583'
    images = [
        {'name': f'figure{index}.png',
         'url': f'https://attach.munpia.com/figure{index}.png'}
        for index in (1, 2)
    ]
    downloaded = []

    def download(url, _label, session, **_kwargs):
        assert session.headers['Origin'] == 'https://www.munpia.com'
        assert session.headers['Referer'] == book_url
        request = session.prepare_request(requests.Request('GET', url))
        assert request.headers['Cookie'] == 'munpia_fixture=saved-session'
        unrelated = session.prepare_request(requests.Request(
            'GET', 'https://unrelated.example/figure.png',
        ))
        assert 'Cookie' not in unrelated.headers
        downloaded.append(url)
        return png

    dialog = object.__new__(ExternalNovelDialog)
    dialog._book_data = {
        '_munpia': True, 'bookname': 'Image Test', 'author': 'Author',
        'bookUrl': book_url,
    }
    dialog._chapter_results = [{
        'chapterName': 'Chapter 1',
        'chapterUrl': 'https://www.munpia.com/novel/view/564583?neSrl=123',
        'contentHtml': '<p>Text</p>' + ''.join(
            f'<img data-src-address="{image["name"]}">' for image in images
        ),
        'images': images,
    }]
    dialog._scraper = SimpleNamespace(_context=SimpleNamespace(
        cookies=lambda: [{
            'name': 'munpia_fixture', 'value': 'saved-session',
            'domain': '.munpia.com', 'path': '/',
        }],
    ))
    dialog._parent_gui = SimpleNamespace()
    dialog._var_long_image_layout = Setting(False)
    dialog._var_kakao_dedupe_images = Setting(False)
    dialog._var_ext_image_workers = Setting(2)
    dialog._get_output_dir = lambda: str(tmp_path)
    dialog._log = Mock()
    dialog._download_image_python = download
    dialog._generate_txt = Mock()
    pdf = Mock()
    monkeypatch.setattr(DownloaderCore, 'generate_pdf', pdf)

    if output_format == 'cbz':
        dialog._generate_image_archive('Image Test', 'Author')
    else:
        getattr(dialog, f'_generate_{output_format}')('Image Test', 'Author')

    assert sorted(downloaded) == sorted(image['url'] for image in images)
    dialog._generate_txt.assert_not_called()
    if output_format == 'pdf':
        assert pdf.call_args.kwargs['image_map'] == {
            'figure1.png': png, 'figure2.png': png,
        }
    else:
        with zipfile.ZipFile(tmp_path / f'Image Test.{output_format}') as archive:
            embedded = [name for name in archive.namelist() if name.endswith('.png')]
            assert len(embedded) == 2
            assert all(archive.read(name) == png for name in embedded)
