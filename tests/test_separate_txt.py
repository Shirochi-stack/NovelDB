from pathlib import Path
from types import SimpleNamespace

import pytest

from external_dialog import ExternalNovelDialog
from gui import NpiaGUI
from text_output import write_text_output


class Setting:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


@pytest.mark.parametrize('separate', [False, True])
def test_main_txt_exports_requests_and_preserves_text(tmp_path, separate):
    anchor = tmp_path / 'Book.txt'
    gui = SimpleNamespace(
        _output_path=str(anchor), _output_paths={'txt': str(anchor)},
        var_save_html=Setting(False), var_separate_txt=Setting(separate),
        log_message=lambda message: None,
    )
    NpiaGUI._write_text_output(gui, [
        ('Same', '<p>First &amp; second<br>Line</p>', [], None),
        None,
        ('Same', '<p>Third</p>', [], None),
    ], False)
    files = sorted(tmp_path.rglob('*.txt'))
    assert len(files) == (2 if separate else 1)
    assert 'First & second\nLine' in files[0].read_text(encoding='utf-8')
    assert 'Third' in files[-1].read_text(encoding='utf-8')
    if separate:
        assert not anchor.exists()
        assert files[0].name == '0001 Same.txt'
        assert files[1].name == '0003 Same.txt'
        assert all(file.parent == tmp_path / 'Book' for file in files)
        assert not any(path.is_dir() for path in (tmp_path / 'Book').iterdir())
        assert gui._output_paths['txt'] == str(tmp_path / 'Book')


@pytest.mark.parametrize('separate', [False, True])
def test_external_txt_skips_missing_and_locked_requests(tmp_path, separate):
    logs = []
    dialog = SimpleNamespace(
        _get_output_dir=lambda: str(tmp_path), _log=logs.append,
        _var_separate_txt=Setting(separate),
        _var_blank_paragraph_lines=Setting(True),
        _chapter_results=[
            {'chapterName': 'Same', 'contentText': 'First\nSecond'},
            None, {'_locked': True},
            {'chapterName': 'Same', 'contentText': 'Last'},
        ],
    )
    ExternalNovelDialog._generate_txt(dialog, 'Book', 'Author')
    files = sorted(tmp_path.rglob('*.txt'))
    assert len(files) == (2 if separate else 1), logs
    assert 'First\n\nSecond' in files[0].read_text(encoding='utf-8')
    assert 'Last' in files[-1].read_text(encoding='utf-8')
    assert all('Author: Author' in file.read_text(encoding='utf-8') for file in files)
    if separate:
        assert files[-1].name == '0004 Same.txt'
        assert all(file.parent == tmp_path / 'Book' for file in files)


def test_separate_txt_names_cannot_escape_book_folder(tmp_path):
    location = write_text_output(str(tmp_path / 'Book.txt'), [
        (1, '../../bad\\name:\x00?', 'Unicode: 안녕'),
        (2, 'x' * 300, 'Long title'),
    ], True)
    files = list(Path(location).rglob('*.txt'))
    assert len(files) == 2
    assert all(file.parent == Path(location) for file in files)
    assert any(file.read_text(encoding='utf-8') == 'Unicode: 안녕' for file in files)


def test_main_separate_txt_uses_active_download_setting(tmp_path):
    anchor = tmp_path / 'Book.txt'
    gui = SimpleNamespace(
        _output_path=str(anchor), var_save_html=Setting(False),
        var_separate_txt=Setting(False), _active_separate_txt=True,
        log_message=lambda message: None,
    )
    NpiaGUI._write_text_output(gui, [('Chapter', '<p>Body</p>', [], None)], False)
    assert not anchor.exists()
    assert (tmp_path / 'Book' / '0001 Chapter.txt').exists()


@pytest.mark.parametrize('numbered', [False, True])
def test_main_separate_txt_uses_book_number_after_notices(tmp_path, numbered):
    gui = SimpleNamespace(
        _output_path=str(tmp_path / 'Book.txt'), var_save_html=Setting(False),
        var_separate_txt=Setting(True), var_number_chapters=Setting(numbered),
        log_message=lambda message: None,
    )
    results = [('Notice', '<p>Notice</p>', [], True)] * 18 + [
        ('Title', '<p>Chapter 490 body</p>', [], False),
        None,
        ('Last', '<p>Chapter 492 body</p>', [], False),
        ('', '<p>Chapter 493 body</p>', [], False),
    ]
    NpiaGUI._write_text_output(gui, results, False, chapter_start=490, notice_count=18)
    names = {file.name for file in (tmp_path / 'Book').iterdir()}
    assert ('490. Title.txt' if numbered else '0490 Title.txt') in names
    assert ('492. Last.txt' if numbered else '0492 Last.txt') in names
    assert 'Chapter 493.txt' in names
    assert not any(name.startswith('0019 ') for name in names)


def test_external_separate_txt_does_not_add_second_number(tmp_path):
    dialog = SimpleNamespace(
        _get_output_dir=lambda: str(tmp_path), _log=lambda message: None,
        _var_separate_txt=Setting(True), _var_number_chapters=Setting(True),
        _chapter_results=[{'chapterName': 'Title', 'contentText': 'Body', '_chapter_number': 490}],
    )
    ExternalNovelDialog._generate_txt(dialog, 'Book', 'Author')
    assert (tmp_path / 'Book' / '490. Title.txt').exists()


@pytest.mark.parametrize('kind', ['file', 'folder', 'missing'])
def test_saved_output_link_opens_files_and_folders(tmp_path, monkeypatch, kind):
    import external_dialog

    path = tmp_path / 'Output'
    if kind == 'file':
        path.write_text('Body', encoding='utf-8')
    elif kind == 'folder':
        path.mkdir()
    opened = []
    errors = []
    monkeypatch.setattr(external_dialog.sys, 'platform', 'win32')
    monkeypatch.setattr(external_dialog.os, 'startfile', opened.append, raising=False)
    dialog = SimpleNamespace(_append_log=errors.append)
    ExternalNovelDialog._open_saved_file(dialog, str(path))
    assert opened == ([] if kind == 'missing' else [str(path)])
    assert bool(errors) == (kind == 'missing')
