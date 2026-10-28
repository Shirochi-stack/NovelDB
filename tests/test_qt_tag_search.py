"""Tag search dialog: two columns, query groups built by drag and drop, persisted in settings."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt")

from noveldb.core.npia import tags as T  # noqa: E402


@pytest.fixture
def dialog(tmp_path, monkeypatch, qtbot):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext
    from noveldb.qt.tag_search import TagSearchDialog

    ctx = AppContext(start_workers=False)
    dlg = TagSearchDialog(ctx)
    qtbot.addWidget(dlg)
    yield dlg
    ctx._save_timer.stop()


def tag(label):
    return next(t for t, l in T.COMMON_TAGS if l == label)


def test_clicking_a_tag_adds_it_as_its_own_group(dialog):
    dialog.chips[tag("Fantasy")].click()
    dialog.chips[tag("Harem")].click()
    assert dialog.groups == [[tag("Fantasy")], [tag("Harem")]]
    assert dialog.context.settings.npia.tag_query_groups == ((tag("Fantasy"),), (tag("Harem"),))


def test_dropping_ors_into_a_group_moves_between_groups_and_removes(dialog):
    fantasy, harem, school = tag("Fantasy"), tag("Harem"), tag("School")
    dialog._drop_on_new_group(fantasy, -1)
    dialog._drop_on_group(harem, -1, 0)          # palette -> group 1: OR
    dialog._drop_on_new_group(school, -1)        # -> group 2: AND
    assert dialog.groups == [[fantasy, harem], [school]]
    dialog._drop_on_group(harem, 0, 1)           # move between groups
    assert dialog.groups == [[fantasy], [school, harem]]
    dialog._drop_on_palette(school, 1)           # back onto the tags removes it
    dialog._remove_from_group(fantasy, 0)        # the x removes too; empty groups go away
    assert dialog.groups == [[harem]]


def test_drop_area_accepts_a_real_drop_event(dialog, qtbot):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QDropEvent

    from noveldb.qt.tag_search import DropArea, _tag_payload

    new_group = next(w for w in dialog.groups_box.findChildren(DropArea) if w.objectName() == "newGroup")
    mime = _tag_payload(tag("Romance"))  # kept alive: the event does not own it (a real QDrag does)
    event = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    new_group.dropEvent(event)
    assert dialog.groups == [[tag("Romance")]]


def test_two_columns_and_custom_tags(dialog):
    from PySide6.QtWidgets import QSplitter

    assert any(s.orientation().name == "Horizontal" and s.count() == 2 for s in dialog.findChildren(QSplitter))
    dialog.custom.setText("my tag, other")
    dialog._add_custom()
    assert "my tag" in dialog.chips and "other" in dialog.chips
    assert dialog.context.settings.npia.tag_custom_tags == "my tag, other"
    dialog.filter.setText("my t")
    assert list(dialog.chips) == ["my tag"]


def test_age_filter_drops_the_adult_tag_from_the_query(dialog):
    dialog._drop_on_new_group(T.ADULT_TAG, -1)
    dialog._drop_on_group(tag("Fantasy"), -1, 0)
    dialog.age.setCurrentIndex(dialog.age.findData("15"))
    assert dialog.groups == [[tag("Fantasy")]]
    assert not dialog.chips[T.ADULT_TAG].isEnabled()
