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

    assert entries() == ["Profile #1", "New Profile", "Delete Profile #1..."] and button.text() == "Profile #1"
    assert not window.download.delete_profile.isEnabled()  # the only profile stays
    window.download.profile_menu.actions()[-2].trigger()  # New Profile
    assert entries() == ["Profile #1", "Profile #2", "New Profile", "Delete Profile #2..."]
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

def _profile_folders(data_dir, count):
    """Profiles 1..count, each folder holding a file that says whose it is."""
    for number in range(1, count + 1):
        folder = data_dir / ("browser_data" if number == 1 else f"browser_data_{number}")
        folder.mkdir()
        (folder / "whose.txt").write_text(f"profile {number}")


def _whose(data_dir, number):
    folder = data_dir / ("browser_data" if number == 1 else f"browser_data_{number}")
    return (folder / "whose.txt").read_text() if (folder / "whose.txt").exists() else None


def test_a_profile_found_signed_out_is_noted_until_it_may_be_signed_in_again(data_dir):
    from noveldb.core.external import profile_accounts

    base = str(data_dir)
    assert profile_accounts.signed_out(base) == set()
    profile_accounts.set_signed_out(3, True, base)
    profile_accounts.set_signed_out(5, True, base)
    assert profile_accounts.signed_out(base) == {3, 5}
    assert ExternalScraper.kpage_profiles_signed_out() == {3, 5}
    profile_accounts.remember(3, "name@example.com", base)  # a download saw it signed in
    assert profile_accounts.signed_out(base) == {5}
    profile_accounts.set_signed_out(5, False, base)  # Enter Browser was opened on it
    assert profile_accounts.signed_out(base) == set()
    assert not (data_dir / "profile_signed_out.json").exists()


def test_deleting_a_profile_removes_its_folder_and_moves_the_later_ones_up(data_dir):
    from noveldb.core.external import profile_accounts, profiles, saved_logins

    base = str(data_dir)
    _profile_folders(data_dir, 4)
    for number, account in ((1, "one"), (2, "two"), (4, "four")):
        profile_accounts.remember(number, account, base)
    profile_accounts.set_signed_out(3, True, base)
    protected = saved_logins.available()
    if protected:
        saved_logins.save("kpage", 2, "two@example.com", "pw2", base)
        saved_logins.save("kpage", 4, "four@example.com", "pw4", base)

    profiles.delete(2, 4, base)
    assert [_whose(data_dir, number) for number in (1, 2, 3, 4)] == ["profile 1", "profile 3", "profile 4", None]
    assert not [name for name in os.listdir(base) if "moving" in name]  # nothing left set aside
    assert profile_accounts.load(base) == {1: "one", 3: "four"}
    assert profile_accounts.signed_out(base) == {2}  # the signed-out profile 3 is profile 2 now
    if protected:
        assert saved_logins.accounts("kpage", base) == {3: "four@example.com"}
        assert saved_logins.get("kpage", 3, base) == ("four@example.com", "pw4")

    profiles.delete(1, 3, base)  # the first profile: the next one takes the plain folder
    assert [_whose(data_dir, number) for number in (1, 2, 3)] == ["profile 3", "profile 4", None]
    assert profile_accounts.load(base) == {2: "four"}

    with pytest.raises(ValueError):
        profiles.delete(1, 1, base)  # the only profile stays
    with pytest.raises(ValueError):
        profiles.delete(5, 2, base)


def test_deleting_the_first_profile_with_no_other_folder_leaves_a_fresh_one(data_dir):
    from noveldb.core.external import profiles

    _profile_folders(data_dir, 1)  # profile 2 exists in the settings but was never opened
    profiles.delete(1, 2, str(data_dir))
    assert _whose(data_dir, 1) is None
    assert os.listdir(data_dir / "browser_data")  # not empty: an old build's folder is not copied in


@pytest.mark.skipif(os.name != "nt", reason="a folder with an open file can only not be moved on Windows")
def test_a_profile_a_browser_has_open_is_not_deleted_and_nothing_moves(data_dir):
    from noveldb.core.external import profile_accounts, profiles

    base = str(data_dir)
    _profile_folders(data_dir, 3)
    profile_accounts.remember(3, "three", base)
    with open(data_dir / "browser_data_3" / "whose.txt") as held:  # as a browser holds its files
        with pytest.raises(profiles.ProfileInUse, match="#3"):
            profiles.delete(2, 3, base)
        assert held.read() == "profile 3"
    assert [_whose(data_dir, number) for number in (1, 2, 3)] == ["profile 1", "profile 2", "profile 3"]
    assert profile_accounts.load(base) == {3: "three"}
    profiles.delete(2, 3, base)  # closed: it goes
    assert [_whose(data_dir, number) for number in (1, 2, 3)] == ["profile 1", "profile 3", None]


def test_the_profile_menu_deletes_the_selected_profile(data_dir, tmp_path_factory, monkeypatch, qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path_factory.mktemp("home")))
    from PySide6.QtWidgets import QMessageBox

    from noveldb.core.external import profile_accounts
    from noveldb.qt.context import AppContext
    from noveldb.qt.pages.download import DownloadPage

    _profile_folders(data_dir, 3)
    profile_accounts.remember(2, "two@example.com", str(data_dir))
    profile_accounts.remember(3, "three@example.com", str(data_dir))
    context = AppContext(start_workers=False)
    context.update_settings(lambda s: replace(s, sources=replace(s.sources, browser_profiles=3, browser_profile=2)))
    page = DownloadPage(context)
    qtbot.addWidget(page)
    page._refresh_profiles()
    assert page.delete_profile.text() == "Delete Profile #2..." and page.delete_profile.isEnabled()

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args[2]) or QMessageBox.Cancel)
    page.delete_profile.trigger()
    assert "two@example.com" in asked[0] and "Profile #3 becomes #2" in asked[0]
    assert context.settings.sources.browser_profiles == 3 and _whose(data_dir, 2) == "profile 2"  # Cancel: nothing

    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.delete_profile.trigger()
    sources = context.settings.sources
    assert (sources.browser_profiles, sources.browser_profile) == (2, 2)
    assert [_whose(data_dir, number) for number in (1, 2, 3)] == ["profile 1", "profile 3", None]
    assert page.profile.text() == "Profile #2 (three@example.com)"

    # A download in progress uses the profiles: nothing is deleted then.
    from noveldb.core.queue.model import RUNNING, Job

    context.runner._jobs.append(Job("https://example.com/b", state=RUNNING))
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: told.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("asked during a download"))
    page.delete_profile.trigger()
    assert "download" in told[0] and context.settings.sources.browser_profiles == 2
    context.runner._jobs.clear()

    # The last profile stays.
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.delete_profile.trigger()
    page._refresh_profiles()
    assert context.settings.sources.browser_profiles == 1 and not page.delete_profile.isEnabled()
    context._save_timer.stop()
