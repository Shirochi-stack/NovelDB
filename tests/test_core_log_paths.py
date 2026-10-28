"""Log lines that name a file or folder: the path is found even with spaces, brackets and a size."""

import os

from noveldb.core.log_paths import find_path


def test_finds_existing_files_and_folders_in_log_lines(tmp_path):
    book = tmp_path / "[433986] 랭커들이 집착하는 대장장이가 되었다.epub"
    book.write_text("x", encoding="utf-8")
    folder = tmp_path / "[433986] 랭커들이 집착하는 대장장이가 되었다"
    folder.mkdir()
    for line, expected in (
        (f"Output file: {book} (2.96 MB)", str(book)),
        (f"TXT output folder: {folder}", str(folder)),
        ("✅ Saved: " + str(book).replace(os.sep, "/"), str(book).replace(os.sep, "/")),
        (f"Output file: {book} (8.0 KB) and more words", str(book)),
    ):
        start, end, path = find_path(line)
        assert path == expected and line[start:end] == path


def test_ignores_lines_without_an_existing_path(tmp_path):
    assert find_path("Download Complete!") is None
    assert find_path(f"Output file: {tmp_path / 'missing.epub'} (1 KB)") is None
    assert find_path("FINAL SUMMARY: SUCCESS | failed chapters: 0") is None
