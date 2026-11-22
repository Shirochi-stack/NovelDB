"""Saved sign-ins: the protected store, and a signed-out kpage profile signing in again by itself."""

import json
import os
from types import SimpleNamespace

import pytest

from noveldb.core.external import saved_logins

protected = pytest.mark.skipif(not saved_logins.available(), reason="no password protection on this system")


@protected
def test_a_saved_sign_in_comes_back_and_its_password_is_never_in_the_file(tmp_path):
    base = str(tmp_path)
    assert saved_logins.accounts("kpage", base) == {} and saved_logins.get("kpage", 2, base) is None
    assert saved_logins.save("kpage", 2, " name@example.com ", "s3cret pass", base)
    assert saved_logins.save("kpage", 5, "other", "another", base)
    assert saved_logins.accounts("kpage", base) == {2: "name@example.com", 5: "other"}
    assert saved_logins.get("kpage", 2, base) == ("name@example.com", "s3cret pass")
    text = (tmp_path / "saved_logins.json").read_text(encoding="utf-8")
    assert "s3cret" not in text and "another" not in text and "name@example.com" in text
    assert saved_logins.accounts("other", base) == {}  # per site
    # Saving again replaces; forgetting removes.
    assert saved_logins.save("kpage", 2, "name@example.com", "changed", base)
    assert saved_logins.get("kpage", 2, base)[1] == "changed"
    assert saved_logins.remove("kpage", 2, base) and not saved_logins.remove("kpage", 2, base)
    assert saved_logins.accounts("kpage", base) == {5: "other"}
    saved_logins.remove("kpage", 5, base)
    assert json.loads((tmp_path / "saved_logins.json").read_text(encoding="utf-8")) == {}


def test_nothing_is_saved_without_an_account_or_a_password(tmp_path):
    base = str(tmp_path)
    assert not saved_logins.save("kpage", 1, "", "password", base)
    assert not saved_logins.save("kpage", 1, "name", "", base)
    assert not (tmp_path / "saved_logins.json").exists()
    # A damaged file reads as empty.
    (tmp_path / "saved_logins.json").write_text("not json", encoding="utf-8")
    assert saved_logins.accounts("kpage", base) == {}


@protected
def test_the_saved_sessions_page_saves_and_forgets_a_profiles_account(tmp_path, monkeypatch, qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NPIA_BROWSER_DATA_DIR", str(tmp_path / "data"))
    from dataclasses import replace

    from PySide6.QtWidgets import QLineEdit

    from noveldb.core.external import profile_accounts
    from noveldb.qt.context import AppContext
    from noveldb.qt.pages.settings import Form, saved_sign_in_section

    context = AppContext(start_workers=False)
    context.update_settings(lambda s: replace(s, sources=replace(s.sources, browser_profiles=3, browser_profile=2)))
    profile_accounts.remember(2, "name@example.com")
    form = Form(context, "Saved sessions")
    qtbot.addWidget(form)
    saved_sign_in_section(form, context)
    form.finish()
    profile, account, password, save, forget, status = form.sign_in
    assert profile.count() == 3 and profile.currentData() == 2  # the profile in use
    assert account.text() == "name@example.com"  # the account it was last seen with
    assert password.echoMode() == QLineEdit.Password and not forget.isEnabled()
    save.click()
    assert "first" in status.text() and saved_logins.accounts("kpage") == {}
    password.setText("pw")
    save.click()
    assert saved_logins.get("kpage", 2) == ("name@example.com", "pw")
    assert password.text() == "" and forget.isEnabled()
    assert "profile #2 (name@example.com)" in status.text() and "✓" in profile.currentText()
    forget.click()
    assert saved_logins.accounts("kpage") == {} and "No account saved" in status.text()
    context._save_timer.stop()


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("NPIA_BROWSER_DATA_DIR", str(tmp_path))
    import external_scraper
    from external_scraper import ExternalScraper

    monkeypatch.setattr(ExternalScraper, "browser_profile", 3)
    clock = [0.0]
    monkeypatch.setattr(external_scraper.time, "monotonic", lambda: clock[0])
    lines = []
    made = ExternalScraper(logger=lines.append)
    made.lines = lines
    made.clock = clock
    made._backup_storage_state = lambda: None
    yield made
    made._page = None


class SignInPage:
    """The site as the sign-in sees it: the account page, its button, the account form."""

    def __init__(self, scraper, outcome="in", session_alive=False):
        self.scraper = scraper
        self.domain = scraper._KPAGE_COOKIE_DOMAIN
        self.url = "about:blank"
        self.outcome = outcome
        self.session_alive = session_alive
        self.signed_in = False
        self.typed = {}
        self.kept = False

    def goto(self, url, **_kwargs):
        self.url = url

    def wait_for_timeout(self, ms):
        self.scraper.clock[0] += ms / 1000

    def evaluate(self, script, *_args):
        if script == self.scraper._KPAGE_ACCOUNT_JS:
            return ({"status": 200, "email": "name@example.com"} if self.signed_in
                    else {"status": 200, "signedOut": True})
        if script == self.scraper._KPAGE_SIGN_IN_STATE_JS:
            return {"captcha": self.outcome == "captcha", "form": True, "path": "/login/",
                    "error": "Wrong password" if self.outcome == "refused" else ""}
        self.kept = True  # the "stay signed in" box
        return None

    def get_by_role(self, role, name, exact):
        def click(timeout=None):
            if self.session_alive:  # the sign-in host still knows the account: straight back
                self.signed_in, self.url = True, f"https://page.{self.domain}/account/info/"
            else:
                self.url = f"https://accounts.{self.domain}/login/?continue=x"

        return SimpleNamespace(last=SimpleNamespace(click=click))

    def locator(self, selector):
        page = self

        class Found:
            first = None

            def count(self):
                return 1

            def fill(self, value):
                page.typed[selector] = value

            def click(self):
                if page.outcome == "in":
                    page.signed_in, page.url = True, f"https://page.{page.domain}/"

        found = Found()
        found.first = found
        return found


def test_a_signed_out_profile_signs_in_again_with_its_saved_account(scraper):
    page = scraper._page = SignInPage(scraper)
    assert scraper.kpage_sign_in("name@example.com", "pw") == ""
    assert page.typed == {'input[name="loginId"]': "name@example.com", 'input[name="password"]': "pw"}
    assert page.kept  # "stay signed in" is ticked
    assert not any("pw" == line or "pw " in line for line in scraper.lines)  # the password is never logged


def test_a_live_account_session_signs_in_without_typing_anything(scraper):
    page = scraper._page = SignInPage(scraper, session_alive=True)
    assert scraper.kpage_sign_in("name@example.com", "pw") == ""
    assert page.typed == {}


@pytest.mark.parametrize("outcome, said", [("captcha", "captcha"), ("refused", "Wrong password"), ("stuck", "another step")])
def test_a_sign_in_that_needs_a_person_is_reported_not_forced(scraper, outcome, said):
    scraper._page = SignInPage(scraper, outcome=outcome)
    assert said in scraper.kpage_sign_in("name@example.com", "pw")


class RememberedPage(SignInPage):
    """The sign-in host lists the accounts this browser remembers, then "log in with a new account"."""

    def __init__(self, scraper, remembered=("first@example.com", "second@example.com"), then="in"):
        super().__init__(scraper)
        self.remembered = list(remembered)
        self.then = then
        self.chosen = []

    def get_by_role(self, role, name, exact):
        def click(timeout=None):
            self.url = f"https://accounts.{self.domain}/login/simple/?continue=x"

        return SimpleNamespace(last=SimpleNamespace(click=click))

    def locator(self, selector):
        if "wrap_profile" not in selector:
            return super().locator(selector)
        names = self.remembered + ([] if "btn_delete" in selector else ["Log in with a new account"])

        def entry(name):
            def click():
                self.chosen.append(name)
                if name in self.remembered and self.then == "in":
                    self.signed_in, self.url = True, f"https://page.{self.domain}/"
                else:  # the site wants the password after all, or a new account: its form
                    self.url = f"https://accounts.{self.domain}/login/?continue=x"

            return SimpleNamespace(click=click, inner_text=lambda: f"{name} Nickname")

        entries = [entry(name) for name in names]
        return SimpleNamespace(count=lambda: len(entries), first=entries[0] if entries else None,
                               last=entries[-1] if entries else None, nth=lambda index: entries[index])


def test_the_account_the_browser_remembers_is_chosen_and_nothing_is_typed(scraper):
    page = scraper._page = RememberedPage(scraper)
    assert scraper.kpage_sign_in() == ""
    assert page.chosen == ["first@example.com"] and page.typed == {}


def test_the_saved_account_is_the_one_chosen_among_the_remembered(scraper):
    page = scraper._page = RememberedPage(scraper)
    assert scraper.kpage_sign_in("second@example.com", "pw") == ""
    assert page.chosen == ["second@example.com"] and page.typed == {}


def test_a_remembered_account_that_wants_its_password_needs_the_saved_one(scraper):
    page = scraper._page = RememberedPage(scraper, then="form")
    assert "password" in scraper.kpage_sign_in()  # nothing saved: said, not guessed
    assert page.typed == {}
    page = scraper._page = RememberedPage(scraper, then="form")
    assert scraper.kpage_sign_in("first@example.com", "pw") == ""
    assert page.typed == {'input[name="loginId"]': "first@example.com", 'input[name="password"]': "pw"}


def test_a_browser_that_remembers_no_account_goes_to_the_form_only_with_a_saved_account(scraper):
    page = scraper._page = RememberedPage(scraper, remembered=())
    assert "remembers no account" in scraper.kpage_sign_in()
    assert page.chosen == []
    page = scraper._page = RememberedPage(scraper, remembered=())
    assert scraper.kpage_sign_in("name@example.com", "pw") == ""
    assert page.chosen == ["Log in with a new account"] and page.typed['input[name="password"]'] == "pw"


def test_a_profile_with_nothing_to_sign_in_with_is_noted_for_auto_purchase(scraper):
    from noveldb.core.external import profile_accounts

    base = os.environ["NPIA_BROWSER_DATA_DIR"]
    scraper._page = RememberedPage(scraper, remembered=())
    assert scraper.kpage_restore_sign_in() is False
    assert profile_accounts.signed_out(base) == {3} and scraper.kpage_profiles_signed_out() == {3}
    assert any("no account to sign in with" in line for line in scraper.lines)
    # A sign-in that only needs a person (a captcha) is not that: the profile is asked again.
    profile_accounts.set_signed_out(3, False, base)
    scraper._kpage_sign_in_tried = set()
    scraper.kpage_sign_in = lambda user, password: "the site asked for a captcha"
    assert scraper.kpage_restore_sign_in() is False
    assert profile_accounts.signed_out(base) == set()


@protected
def test_an_account_saved_for_a_signed_out_profile_puts_it_back_in_the_rotation(scraper):
    from noveldb.core.external import profile_accounts

    base = os.environ["NPIA_BROWSER_DATA_DIR"]
    profile_accounts.set_signed_out(3, True, base)
    assert scraper.kpage_profiles_signed_out() == {3}
    saved_logins.save("kpage", 3, "name@example.com", "pw", base)
    assert scraper.kpage_profiles_signed_out() == set()


@protected
def test_restoring_uses_the_account_saved_for_the_profile_once_per_download(scraper):
    base = os.environ["NPIA_BROWSER_DATA_DIR"]
    asked = []
    scraper.kpage_sign_in = lambda user, password: asked.append((user, password)) or ""
    # Nothing saved for profile 3: the account its browser remembers is tried all the same.
    assert scraper.kpage_restore_sign_in() is True and asked == [("", "")]
    assert any("browser remembers" in line for line in scraper.lines)
    asked.clear()
    saved_logins.save("kpage", 3, "name@example.com", "pw", base)
    scraper._kpage_sign_in_tried = set()
    assert scraper.kpage_restore_sign_in() is True
    assert asked == [("name@example.com", "pw")]
    assert scraper.kpage_restore_sign_in() is False and len(asked) == 1  # not sent again
    assert any("signed in again" in line for line in scraper.lines)
    assert not any("pw" in line.split() for line in scraper.lines)
    # A refusal is said, with where to finish it.
    scraper._kpage_sign_in_tried = set()
    scraper.kpage_sign_in = lambda user, password: "the site asked for a captcha"
    assert scraper.kpage_restore_sign_in() is False
    assert any("captcha" in line and "Enter Browser" in line for line in scraper.lines)


@protected
def test_auto_purchase_signs_in_again_and_asks_once_more(scraper):
    base = os.environ["NPIA_BROWSER_DATA_DIR"]
    saved_logins.save("kpage", 3, "name@example.com", "pw", base)
    site = f"https://page.{scraper._KPAGE_COOKIE_DOMAIN}"
    answers = [{"login": True}, {"used": "RT05", "claimed": []}]
    scraper._page = SimpleNamespace(url=f"{site}/content/7", evaluate=lambda script, *args: (
        answers.pop(0) if script == scraper._KPAGE_TICKET_JS else {"status": 200, "email": "name@example.com"}))
    signed = []
    scraper.kpage_sign_in = lambda user, password: signed.append(user) or ""
    chapter = {"url": f"{site}/content/7/viewer/99", "name": "c4"}
    assert scraper.kpage_auto_purchase(chapter) is True
    assert signed == ["name@example.com"] and answers == []
