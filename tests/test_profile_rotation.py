"""Profile rotation: Stop, the kept-open profile browsers, and the account shown per profile."""

import time
from types import SimpleNamespace

import pytest

from noveldb.core.external import profile_accounts
from noveldb.core.external.pipeline import ExternalPipelineMixin
from tests.test_chapter_range_and_purchase import Rotating, _chapters


def test_stop_ends_the_rotation_without_another_ticket_or_a_trip_back():
    job = Rotating([1, 1, 1], selected=1, low=4, high=6)
    purchase = job._scraper.kpage_auto_purchase
    released = []

    def stopping(chapter):
        outcome = purchase(chapter)
        if chapter["name"] == "c5":
            job._downloading = False  # Stop pressed while profile 2 worked
        return outcome

    job._scraper.kpage_auto_purchase = stopping
    job._scraper.kpage_release_profiles = lambda: released.append(True)
    fetched = ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert job.bought == [(1, "c4"), (2, "c5")]  # c6 is never asked for
    assert sorted(fetched) == [3]
    assert job.switches == [2]  # no switch back to profile 1, none on to profile 3
    assert released == [True]  # the kept-open browsers are closed all the same


def test_stop_while_a_profile_opens_uses_no_ticket():
    job = Rotating([1, 1], selected=1, low=4, high=5)
    switch = job._scraper.kpage_switch_profile

    def slow_switch(number):
        switch(number)
        job._downloading = False

    job._scraper.kpage_switch_profile = slow_switch
    ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert job.bought == [(1, "c4")]


def test_the_rotation_closes_the_other_browsers_when_it_ends():
    job = Rotating([1, 1, 1], low=4, high=6)
    released = []
    job._scraper.kpage_release_profiles = lambda: released.append(job.profile)
    ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert released == [1]  # after the selected profile is in use again


def _with_addresses(chapters):
    for number, chapter in enumerate(chapters, 1):
        chapter["url"] = f"https://example.com/content/7/viewer/{number}"
    return chapters


def test_a_chapter_another_profile_unlocked_gets_its_images_with_that_profiles_sign_in(monkeypatch):
    import requests

    job = Rotating([1, 1, 1], low=4, high=6)
    job._copy_browser_cookies_to_session = (
        lambda session, quiet=False: ExternalPipelineMixin._copy_browser_cookies_to_session(job, session, quiet))
    # Each profile's browser holds its own account's cookie.
    job._scraper._context = SimpleNamespace(cookies=lambda: [
        {"name": "account", "value": str(job.profile), "domain": ".example.com", "path": "/"}])
    address = "https://img.example.com/{}.jpg?a=1&amp;b=2".format
    job._scraper.parse_chapter = lambda position, chapter, log_errors=True: {
        "chapterName": chapter["name"], "images": [{"url": address(chapter["name"])}]}
    ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert job.profile == 1  # the rest of the download runs as the selected profile
    assert not any("browser cookie(s)" in line for line in job.lines)

    asked = []

    def get(self, url, **options):
        asked.append((url.rsplit("/", 1)[1].split(".")[0], self.cookies.get("account")))
        return SimpleNamespace(status_code=200, content=b"x" * 200, headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(requests.Session, "get", get)
    with requests.Session() as selected:
        selected.cookies.set("account", "1", domain=".example.com")
        for name in ("c4", "c5", "c6"):
            assert ExternalPipelineMixin._download_image_python(job, address(name), session=selected, log_success=False)
        # Profile 1 unlocked chapter 4; chapters 5 and 6 belong to the accounts of profiles 2 and 3.
        assert asked == [("c4", "1"), ("c5", "2"), ("c6", "3")]
        ExternalPipelineMixin._forget_image_owners(job)  # the download is over
        ExternalPipelineMixin._download_image_python(job, address("c5"), session=selected, log_success=False)
        assert asked[-1] == ("c5", "1")


def test_a_profile_that_already_has_a_chapter_open_downloads_it_without_a_ticket():
    # A second download of the same chapters: every ticket is used up, but profiles 2 and 3 still
    # have the chapters open that they unlocked the first time.
    job = Rotating([0, 0, 0], low=4, high=6)
    chapters = _with_addresses(_chapters(8))
    has_open = {2: {chapters[4]["url"]}, 3: {chapters[5]["url"], chapters[0]["url"]}}
    job._scraper.kpage_open_episodes = lambda asked: has_open.get(job.profile, set())
    fetched = ExternalPipelineMixin._auto_purchase(job, chapters, 0, [None] * 8)
    assert job.bought == []
    assert job.read == [(2, "c5"), (3, "c6")]
    assert sorted(fetched) == [4, 5]  # chapter 4 stays locked: nobody has it or a ticket for it
    assert chapters[4]["isAccessible"] and not chapters[3]["isAccessible"]
    assert any("already has c5 open" in line for line in job.lines)
    assert job.profile == 1


def test_a_profile_out_of_tickets_is_still_asked_what_it_has_open():
    job = Rotating([0, 1, 0], low=4, high=6)
    chapters = _with_addresses(_chapters(8))
    job._scraper.kpage_open_episodes = lambda asked: {chapters[5]["url"]} if job.profile == 3 else set()
    fetched = ExternalPipelineMixin._auto_purchase(job, chapters, 0, [None] * 8)
    assert job.bought == [(2, "c4")]
    assert job.read == [(2, "c4"), (3, "c6")]  # profile 3 had no ticket for c5, but had c6 open
    assert sorted(fetched) == [3, 5]


def test_a_chapter_saved_with_another_accounts_sign_in_is_read_again(tmp_path):
    from tests.test_external_dialog_mpia import Setting, make_download_dialog

    path = tmp_path / "book.json"
    fetched = []

    def fetch(batch, *, success_callback, **options):
        fetched.append([chapter["id"] for chapter in batch])
        result = {"chapterName": "One", "images": [{"url": "https://test.invalid/image"}]}
        success_callback(0, result)
        return [result]

    def download(locked):
        job = make_download_dialog(SimpleNamespace(_context=True, parse_chapter_batch=fetch), {"_kpage": True})
        job._var_use_cache = Setting(True)
        job._external_cache_path = lambda: str(path)
        chapter = {"id": "1", "name": "One", "isVIP": locked, "isPaid": locked, "isAccessible": not locked}
        ExternalPipelineMixin._do_download(job, [chapter], 0, 1, 0, num_threads=2)
        return " ".join(str(call.args[0]) for call in job._log.call_args_list)

    download(locked=False)
    assert "another account" not in download(locked=False) and len(fetched) == 1  # the saved copy is used
    # The same chapter, locked for the account now in use: its images would all be refused.
    assert "another account" in download(locked=True)
    assert len(fetched) == 2


def test_a_saved_chapter_whose_image_addresses_ran_out_is_read_again():
    expired = ExternalPipelineMixin._cached_image_addresses_expired
    image = "https://img.example.com/1.jpg?kid=a&amp;expires={}&amp;signature=b".format
    now = 1_795_000_000
    assert expired({"images": [{"url": image(now - 5)}]}, now)
    assert expired({"images": [{"url": image(now + 60)}]}, now)  # too little time left to download it
    assert not expired({"images": [{"url": image(now + 7200)}]}, now)
    assert expired({"images": [{"url": image((now - 5) * 1000)}]}, now)  # milliseconds
    assert not expired({"images": [{"url": image(now - 5), "data": "data:image/png;base64,AA=="}]}, now)
    assert not expired({"images": [{"url": "https://img.example.com/1.jpg?expires=never"}]}, now)
    assert not expired({"images": [{"url": "https://img.example.com/1.jpg"}], "contentText": "text"}, now)
    assert not expired({"contentText": "text"}, now) and not expired(None, now)


def test_accounts_are_noted_per_profile(tmp_path):
    base = str(tmp_path)
    assert profile_accounts.load(base) == {}
    assert profile_accounts.remember(3, "name@example.com", base) is True
    assert profile_accounts.remember(3, "name@example.com", base) is False  # nothing new
    profile_accounts.remember(1, "other", base)
    accounts = profile_accounts.load(base)
    assert accounts == {1: "other", 3: "name@example.com"}
    assert profile_accounts.label(3, accounts) == "Profile #3 (name@example.com)"
    assert profile_accounts.label(2, accounts) == "Profile #2"
    profile_accounts.remember(3, "", base)  # signed out
    assert profile_accounts.load(base) == {1: "other"}


def test_the_job_says_stopping_once():
    from noveldb.core.external.job import ExternalJob
    from noveldb.core.settings.model import AppSettings

    lines = []
    job = ExternalJob("https://example.com/b", AppSettings())
    job._log = lines.append
    job._scraper = SimpleNamespace(_stop_requested=False)
    job.stop()
    job.stop()
    assert len([line for line in lines if line.startswith("Stopping")]) == 1
    assert job._scraper._stop_requested and not job._downloading


def _stoppable_job(generate_on_stop):
    from dataclasses import replace

    from noveldb.core.events import CancelToken
    from noveldb.core.external.job import ExternalJob
    from noveldb.core.settings.model import AppSettings

    settings = AppSettings()
    settings = replace(settings, shared=replace(settings.shared, generate_on_stop=generate_on_stop))
    token = CancelToken()
    job = ExternalJob("https://example.com/b", settings, cancel=token)
    job.lines = []
    job._log = job.lines.append
    return job, token


def test_a_second_press_of_stop_gives_up_the_partial_output(monkeypatch):
    from noveldb.core import events

    clock = [100.0]
    monkeypatch.setattr(events.time, "monotonic", lambda: clock[0])
    job, token = _stoppable_job(generate_on_stop=True)

    token.cancel()
    clock[0] += 0.2
    token.cancel()  # a double click is one press
    job.stop()
    job.stop()  # the dispatcher asks again every quarter second while Stop is in force
    assert token.presses == 1 and not job._output_stopped
    # "Generate on stop" builds the output from here: the press that started it does not stop it.
    job._generation_stop_baseline = True
    assert not job._generation_stopped()

    clock[0] += 5
    token.cancel()  # Stop again, while the output is being built
    job.stop()
    job.stop()
    assert job._output_stopped and job._generation_stopped()
    assert len([line for line in job.lines if "Stop pressed again" in line]) == 1


@pytest.mark.parametrize("presses, built", [(1, ["output"]), (2, [])])
def test_stop_twice_before_the_output_starts_skips_it(presses, built):
    job, token = _stoppable_job(generate_on_stop=True)
    data = {"bookname": "Stopped book", "author": "Author", "chapterCount": 2,
            "chapters": [{"name": "One"}, {"name": "Two"}]}
    job._scraper = SimpleNamespace(prepare_download_browser=lambda: None, parse_book=lambda url: data,
                                   _stop_requested=False)
    job._apply_scraper_options = lambda: None
    generated = []
    job._generate_output = lambda: generated.append("output")

    def stopped_after_one(*args, **kwargs):
        job._chapter_results = [{"chapterName": "One", "contentText": "Downloaded"}, None]
        token.presses = presses
        job.stop()

    job._do_download = stopped_after_one
    ExternalPipelineMixin._do_fetch_and_download(job, "https://example.com/book", 0, 2, False)
    assert generated == built


def test_one_press_of_stop_says_nothing_about_the_output_without_generate_on_stop():
    job, token = _stoppable_job(generate_on_stop=False)
    token.presses = 2
    job.stop()
    assert job._output_stopped and not any("Stop pressed again" in line for line in job.lines)


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("NPIA_BROWSER_DATA_DIR", str(tmp_path))
    import external_scraper
    from external_scraper import ExternalScraper

    monkeypatch.setattr(ExternalScraper, "browser_profile", 1)
    lines = []
    made = ExternalScraper(logger=lines.append)
    made.lines = lines
    try:
        yield made
    finally:
        made.cleanup()


def test_a_profile_left_behind_keeps_its_browser_and_coming_back_is_immediate(scraper):
    from external_scraper import ExternalScraper

    try:
        scraper.start()
    except Exception as exc:  # no browser installed on this machine
        pytest.skip(f"headless browser unavailable: {exc}")
    first = scraper._page
    scraper._kpage_claimed = {"1": ["RT05"]}
    assert scraper.kpage_switch_profile(2)
    second = scraper._page
    assert second is not first and ExternalScraper.browser_profile == 2
    assert first.evaluate("1 + 1") == 2  # profile 1's browser is still open
    assert scraper._kpage_claimed == {}  # another account: its tickets are asked for again

    started = time.perf_counter()
    assert scraper.kpage_switch_profile(1)
    assert time.perf_counter() - started < 1.0
    assert scraper._page is first and scraper._kpage_claimed == {"1": ["RT05"]}
    assert list(scraper._profile_pool) == [2]
    assert "browser_data_2" in "".join(scraper.lines)

    scraper.kpage_release_profiles()
    assert scraper._profile_pool == {}
    with pytest.raises(Exception):
        second.evaluate("1")  # closed
    assert scraper._page.evaluate("1") == 1  # the profile in use keeps its browser


def test_cleanup_closes_every_kept_open_browser(scraper):
    try:
        scraper.start()
    except Exception as exc:
        pytest.skip(f"headless browser unavailable: {exc}")
    first = scraper._page
    scraper.kpage_switch_profile(2)
    scraper.cleanup()
    assert scraper._profile_pool == {} and scraper._context is None and scraper._playwright is None
    with pytest.raises(Exception):
        first.evaluate("1")
    # A later start works again (a new driver).
    scraper.start()
    assert scraper._page.evaluate("2") == 2


def test_the_profile_in_use_says_which_locked_chapters_it_already_has_open(scraper):
    host = "page." + scraper._KPAGE_COOKIE_DOMAIN
    episodes = [{"productId": 11, "isAccessible": True, "isFree": False},
                {"productId": 12, "isAccessible": False, "isFree": False},
                {"productId": 13, "isAccessible": True, "isFree": False},  # not one that was asked about
                {"productId": 15, "isAccessible": True, "isFree": True}]
    asked = []
    scraper._page = SimpleNamespace(url=f"https://{host}/content/7",
                                    evaluate=lambda script, series: asked.append((script, series)) or episodes)
    chapters = [{"url": f"https://{host}/content/7/viewer/{number}"} for number in (11, 12, 15)]
    assert scraper.kpage_open_episodes(chapters) == {chapters[0]["url"]}
    assert asked == [(scraper._KPAGE_EPISODES_JS, "7")]
    assert "product/list" in scraper._KPAGE_EPISODES_JS and scraper._KPAGE_COOKIE_DOMAIN in scraper._KPAGE_EPISODES_JS
    scraper._page = SimpleNamespace(url=f"https://{host}/content/7", evaluate=lambda *_: 1 / 0)
    assert scraper.kpage_open_episodes(chapters) == set()  # the site could not be asked
    scraper._page = None
    assert scraper.kpage_open_episodes(chapters) == set()


def test_a_trailer_video_in_the_episode_list_is_not_a_chapter(scraper):
    video = scraper._kpage_is_video
    assert video({"fullName": "Title 트레일러 영상", "pageCount": 1})
    assert video({"fullName": "Title 티저", "pageCount": 0}) and video({"fullName": "Title PV", "pageCount": 1})
    assert not video({"fullName": "Title 1화", "pageCount": 98})
    assert not video({"fullName": "Title 1화", "pageCount": 1})  # a one-page chapter is a chapter
    assert not video({"fullName": "Title 트레일러 영상", "pageCount": 40})  # named so, but has pages to read
    assert not video({"fullName": "", "name": "Episode 3"})
    assert "pageCount" in scraper._KPAGE_EPISODES_JS


def test_the_account_is_only_asked_for_on_the_site(scraper):
    scraper._page = SimpleNamespace(url="about:blank", evaluate=lambda *_: pytest.fail("asked off the site"))
    assert scraper.kpage_account() == ""
    host = "page." + scraper._KPAGE_COOKIE_DOMAIN
    scraper._page = SimpleNamespace(url=f"https://{host}/content/1",
                                    evaluate=lambda *_: {"status": 200, "email": "name@example.com", "nickname": "n"})
    assert scraper.kpage_account() == "name@example.com"
    assert scraper.kpage_account() == "name@example.com"
    assert len([line for line in scraper.lines if "signed in as name@example.com" in line]) == 1  # said once
    import os

    assert profile_accounts.load(os.environ["NPIA_BROWSER_DATA_DIR"]) == {1: "name@example.com"}
    assert scraper._profile_name(1) == "profile #1 (name@example.com)"
    scraper._page = None
