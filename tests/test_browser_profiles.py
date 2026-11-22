"""Enter Browser profiles: each has its own browser data folder, so its own sign-in sessions."""
import os
from dataclasses import replace

import pytest

from external_scraper import ExternalScraper
from noveldb.core.settings.desktop_config import export_config, import_config
from noveldb.core.settings.legacy_vars import external_values
from noveldb.core.settings.model import AppSettings


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NPIA_BROWSER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ExternalScraper, "browser_profile", 1)
    return tmp_path


def test_each_profile_has_its_own_folder_and_the_first_keeps_the_old_one(data_dir):
    first = ExternalScraper._get_user_data_dir()
    assert first == os.path.join(str(data_dir), "browser_data")
    scraper = ExternalScraper()
    closed = []
    scraper.cleanup = lambda: closed.append(True)
    assert scraper.use_profile(2) == 2
    assert closed == [True]  # an open browser belongs to the profile it started with
    assert ExternalScraper._get_user_data_dir() == os.path.join(str(data_dir), "browser_data_2")
    assert ExternalScraper._get_storage_state_path().startswith(os.path.join(str(data_dir), "browser_data_2"))
    assert ExternalScraper._get_session_cookie_path().startswith(os.path.join(str(data_dir), "browser_data_2"))
    scraper.use_profile(2)
    assert closed == [True]  # nothing to do when the profile is already in use
    scraper.use_profile(1)
    assert ExternalScraper._get_user_data_dir() == first


def test_the_cookie_backup_never_replaces_a_newer_sign_in():
    now = 1_000_000.0
    day = 86400.0

    def cookie(name, expires, value="v", domain=".example.com"):
        return {"name": name, "value": value, "domain": domain, "path": "/", "expires": expires}

    live = [cookie("token", now + 60 * day, "renewed"), cookie("stale", now + 5 * day, "old"),
            cookie("session", -1, "live")]
    backup = [
        cookie("token", now + 58 * day, "replaced"),   # the profile renewed it since: stays out
        cookie("stale", now + 30 * day, "newer"),      # the backup is the newer one: restored
        cookie("session", -1, "saved"),                # the profile has it
        cookie("lost", now + 9 * day),                 # missing from the profile: restored
        cookie("gone", -1),                            # a session cookie the profile dropped
        cookie("expired", now - day),                  # not put back
    ]
    restored = ExternalScraper._backup_cookies_to_restore(backup, live, now)
    assert [(c["name"], c["value"]) for c in restored] == [("stale", "newer"), ("lost", "v"), ("gone", "v")]
    # An empty profile (a lost one) gets everything that has not expired.
    assert len(ExternalScraper._backup_cookies_to_restore(backup, [], now)) == 5


def test_the_selected_profile_is_saved_and_reaches_the_download():
    settings = AppSettings()
    settings = replace(settings, sources=replace(settings.sources, browser_profiles=3, browser_profile=2))
    again = import_config(export_config(settings))
    assert (again.sources.browser_profiles, again.sources.browser_profile) == (3, 2)
    assert external_values(again, "https://example.com/b")["_var_browser_profile"].get() == 2
    # Auto purchase rotates over the profiles unless "Disable profile rotation" is ticked.
    assert external_values(again, "https://example.com/b")["_var_browser_profiles"].get() == 3
    fixed = replace(again, sources=replace(again.sources, disable_profile_rotation=True))
    assert external_values(fixed, "https://example.com/b")["_var_browser_profiles"].get() == 1
    assert import_config(export_config(fixed)).sources.disable_profile_rotation is True
    # A profile number past the count (a hand-edited file) falls back inside it.
    odd = import_config({"ext_browser_profiles": 2, "ext_browser_profile": 9})
    assert (odd.sources.browser_profiles, odd.sources.browser_profile) == (2, 2)
    assert import_config({}).sources.browser_profile == 1
    # There is no upper limit on how many profiles there are.
    many = import_config({"ext_browser_profiles": 250, "ext_browser_profile": 180})
    assert (many.sources.browser_profiles, many.sources.browser_profile) == (250, 180)


def test_download_is_a_red_stop_while_downloads_run(tmp_path, monkeypatch, qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.core.queue.model import QUEUED, Job
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    context = AppContext(start_workers=False)
    window = MainWindow(context)
    qtbot.addWidget(window)
    page = window.download
    button = page.download
    assert button.text().startswith("Download") and button.property("primary") is True

    job = Job("https://example.com/b", source="kpage", state=QUEUED)
    context.runner._jobs.append(job)  # a download waiting or running
    stopped = []
    monkeypatch.setattr(context.runner, "cancel_all", lambda: stopped.append(True))
    page._update_download_button()
    assert button.text() == "Stop" and button.isEnabled()
    assert button.property("danger") is True and button.property("primary") is False
    button.click()
    assert stopped == [True]  # Stop stops; it does not queue anything

    # New links typed while downloads run can still be queued: the button is Download again.
    page.input.setPlainText("https://example.com/another")
    page._text_changed()
    assert button.text().startswith("Download") and button.property("danger") is False

    page.input.setPlainText("")
    context.runner._jobs.clear()
    page._update_download_button()
    assert button.text().startswith("Download") and button.property("primary") is True
    context._save_timer.stop()


def test_new_profile_adds_one_and_selects_it(tmp_path, monkeypatch, qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NPIA_BROWSER_DATA_DIR", str(tmp_path / "data"))  # no accounts seen yet
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    context = AppContext(start_workers=False)
    window = MainWindow(context)
    qtbot.addWidget(window)
    button = window.download.profile

    def entries():
        return [action.text() for action in window.download.profile_menu.actions() if not action.isSeparator()]

    assert entries() == ["Profile #1", "New Profile"] and button.text() == "Profile #1"
    window.download.profile_menu.actions()[-1].trigger()  # New Profile
    assert entries() == ["Profile #1", "Profile #2", "New Profile"]
    assert button.text() == "Profile #2" and context.settings.sources.browser_profile == 2
    window.download.profile_menu.actions()[0].trigger()  # back to the first
    assert context.settings.sources.browser_profile == 1 and button.text() == "Profile #1"
    assert [a.isChecked() for a in window.download.profile_menu.actions()[:2]] == [True, False]
    context._save_timer.stop()


def test_a_profile_shows_the_account_a_download_saw_in_it(tmp_path, monkeypatch, qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NPIA_BROWSER_DATA_DIR", str(tmp_path / "data"))
    from dataclasses import replace

    from noveldb.core.external import profile_accounts
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    context = AppContext(start_workers=False)
    context.update_settings(lambda s: replace(s, sources=replace(s.sources, browser_profiles=2, browser_profile=2)))
    window = MainWindow(context)
    qtbot.addWidget(window)
    page = window.download
    assert page.profile.text() == "Profile #2"
    profile_accounts.remember(2, "name@example.com")  # a download asked the site who is signed in
    page.profile_menu.aboutToShow.emit()  # opening the menu looks again
    assert page.profile.text() == "Profile #2 (name@example.com)"
    assert [a.text() for a in page.profile_menu.actions()[:2]] == ["Profile #1", "Profile #2 (name@example.com)"]
    assert "name@example.com" in page.profile.toolTip()
    profile_accounts.remember(2, "a-very-long-account-name-that-would-push-the-row@example.com")
    page._refresh_profiles()
    assert page.profile.text().endswith("…)") and len(page.profile.text()) < 45