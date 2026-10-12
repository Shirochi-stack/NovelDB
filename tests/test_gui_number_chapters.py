import zipfile
from types import SimpleNamespace

import pytest

from gui import NpiaGUI


class Setting:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


@pytest.mark.parametrize('numbered', [False, True])
@pytest.mark.parametrize('output_format', ['epub', 'txt', 'html', 'pdf'])
def test_numbered_exports_preserve_range_gaps_and_notices(tmp_path, numbered, output_format):
    captured = {}
    results = [
        None,  # Failed notice still occupies its original slot.
        ('Notice', '<p>Notice body</p>', [], True),
        ('Title', '<p>First body</p>', [], False),
        None,  # Failed chapter must not renumber subsequent chapters.
        ('Last', '<p>Last body</p>', [], False),
        ('', '<p>Untitled body</p>', [], False),
    ]
    gui = SimpleNamespace(
        _output_format=output_format, _output_path=str(tmp_path / f'Book.{output_format}'),
        var_number_chapters=Setting(numbered), var_save_html=Setting(output_format == 'html'),
        var_separate_txt=Setting(False), var_zip_compress_images=Setting(False),
        var_save_image_urls_only=Setting(False), var_pdf_toc=Setting(True),
        var_pdf_page_numbers=Setting(False), var_pdf_counter_layout=Setting(False),
        downloader=SimpleNamespace(generate_pdf=lambda *args, **kwargs: captured.update(chapters=args[2])),
        log_message=lambda message: None,
    )
    expected = '20. Title' if numbered else 'Title'
    last = '22. Last' if numbered else 'Last'
    if output_format in ('txt', 'html'):
        NpiaGUI._write_text_output(gui, results, False, chapter_start=20, notice_count=2)
        text = (tmp_path / f'Book.{output_format}').read_text(encoding='utf-8')
        if output_format == 'html':
            assert f'<h2>{expected}</h2>' in text
            assert f'<h2>{last}</h2>' in text
        else:
            assert f'{expected}\n\nFirst body' in text
            assert f'{last}\n\nLast body' in text
        assert 'Chapter 23' in text
        assert 'Notice' in text and '. Notice' not in text
    else:
        NpiaGUI._build_output(gui, results, {'title': 'Book', 'author': 'Author'}, '', None, None,
                              chapter_start=20, notice_count=2)
        if output_format == 'epub':
            with zipfile.ZipFile(gui._output_path) as archive:
                toc = archive.read('OEBPS/toc.ncx').decode('utf-8')
                chapter = archive.read('OEBPS/Text/chapter0020.xhtml').decode('utf-8')
                assert expected in toc and expected in chapter
                assert last in toc and 'Chapter 23' in toc
                assert '. Notice' not in toc
        else:
            assert [chapter['title'] for chapter in captured['chapters']] == [
                'Notice', expected, last, 'Chapter 23',
            ]
    assert results[2][0] == 'Title'  # Cached/source titles stay reusable.


def test_repeated_export_does_not_duplicate_number_prefix(tmp_path):
    gui = SimpleNamespace(
        _output_path=str(tmp_path / 'Book.txt'), var_number_chapters=Setting(True),
        var_save_html=Setting(False), var_separate_txt=Setting(False),
        log_message=lambda message: None,
    )
    results = [('Title', '<p>Body</p>', [], False)]
    for _ in range(2):
        NpiaGUI._write_text_output(gui, results, False, chapter_start=42)
    text = (tmp_path / 'Book.txt').read_text(encoding='utf-8')
    assert text.startswith('42. Title\n')
    assert '42. 42.' not in text
