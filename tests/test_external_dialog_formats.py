from types import SimpleNamespace

from external_dialog import ExternalNovelDialog


class Setting:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def make_dialog(formats, *, long_image_layout=False, has_long_images=False):
    selected = set(formats)
    calls = []
    logs = []
    dialog = SimpleNamespace(
        _book_data={"bookname": "Example", "author": "Author"},
        _var_format=Setting("epub"),
        _var_format_epub=Setting("epub" in selected),
        _var_format_txt=Setting("txt" in selected),
        _var_format_pdf=Setting("pdf" in selected),
        _var_format_cbz=Setting("cbz" in selected),
        _var_long_image_layout=Setting(long_image_layout),
        _log=logs.append,
        _has_long_image_chapters=lambda: has_long_images,
        _generate_epub=lambda title, author: calls.append(("epub", title, author)),
        _generate_txt=lambda title, author: calls.append(("txt", title, author)),
        _generate_pdf=lambda title, author: calls.append(("pdf", title, author)),
        _generate_image_archive=lambda title, author: calls.append(
            ("cbz", title, author)
        ),
    )
    dialog._selected_output_formats = lambda: (
        ExternalNovelDialog._selected_output_formats(dialog)
    )
    return dialog, calls, logs


def test_external_formats_are_returned_in_stable_ui_order():
    dialog, _calls, _logs = make_dialog(["cbz", "txt", "epub"])

    assert ExternalNovelDialog._selected_output_formats(dialog) == [
        "epub",
        "txt",
        "cbz",
    ]


def test_external_output_generates_every_selected_format_once():
    dialog, calls, logs = make_dialog(["epub", "txt", "pdf", "cbz"])

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["epub", "txt", "pdf", "cbz"]
    assert all(call[1:] == ("Example", "Author") for call in calls)
    assert any("EPUB, TXT, PDF, CBZ" in message for message in logs)


def test_external_output_uses_selection_snapshotted_at_download_start():
    dialog, calls, _logs = make_dialog(["epub"])
    dialog._active_output_formats = ["pdf", "cbz"]

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["pdf", "cbz"]


def test_long_image_mode_preserves_legacy_epub_to_cbz_conversion():
    dialog, calls, _logs = make_dialog(
        ["epub"], long_image_layout=True, has_long_images=True
    )

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["cbz"]


def test_explicit_epub_and_cbz_selection_generates_both_in_long_image_mode():
    dialog, calls, _logs = make_dialog(
        ["epub", "cbz"], long_image_layout=True, has_long_images=True
    )

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["epub", "cbz"]
