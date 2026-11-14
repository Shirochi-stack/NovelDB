"""The Tools menu's "Open the Android emulator (ac.qq comics app)": which emulator it opens, and with which app."""

from types import SimpleNamespace

from external_scraper import ExternalScraper
from noveldb.qt import tools


def scraper(devices, installed):
    """A scraper whose emulators are `devices` ([(serial, name)]), with the comics app on those in `installed`."""
    logs, calls = [], []
    subject = ExternalScraper(logger=logs.append)
    subject._android_devices = lambda: list(devices)

    def adb(*args, serial=None, **_kwargs):
        calls.append((serial, args))
        listed = f"package:{args[-1]}\n" if args[:4] == ("shell", "pm", "list", "packages") and serial in installed else ""
        return SimpleNamespace(stdout=listed, stderr="", returncode=0)

    subject._adb = adb
    return subject, logs, calls


def test_the_menu_has_the_entry_beside_the_sfc_ones():
    names = [name for name, _label in tools.TOOLS]
    assert "acqq_android_open" in names
    assert names.index("acqq_android_open") > names.index("sfc_copy")
    assert dict(tools.TOOLS)["acqq_android_open"] == "Open the Android emulator (ac.qq comics app)"
    assert hasattr(tools.Tools, "_acqq_android_open")


def test_a_running_emulator_that_has_the_app_is_used_and_the_others_are_left_alone():
    subject, logs, calls = scraper([("emulator-5554", "other_project"), ("emulator-5556", "noveldb_shots_api35")],
                                   installed={"emulator-5556"})
    opened = []
    subject.open_android_emulator = lambda **kwargs: opened.append(kwargs) or True
    assert subject.open_android_emulator_for_acqq() is True
    assert opened == []  # no other emulator is started
    launched = [(serial, args) for serial, args in calls if args[:2] == ("shell", "monkey")]
    assert [serial for serial, _args in launched] == ["emulator-5556"]
    assert ExternalScraper.ACQQ_ANDROID_PACKAGE in launched[0][1]
    assert any("noveldb_shots_api35 has the ac.qq comics app" in line for line in logs)


def test_with_the_app_on_no_running_emulator_noveldbs_own_is_opened_for_it():
    subject, _logs, calls = scraper([("emulator-5554", "other_project")], installed=set())
    opened = []
    subject.open_android_emulator = lambda **kwargs: opened.append(kwargs) or True
    assert subject.open_android_emulator_for_acqq() is True
    assert opened == [{"package": ExternalScraper.ACQQ_ANDROID_PACKAGE, "label": ExternalScraper.ACQQ_ANDROID_LABEL}]
    assert not any(args[:2] == ("shell", "monkey") for _serial, args in calls)  # nothing is opened on the other project's


def installer(abis="x86_64,arm64-v8a", installed=False, installs=True):
    """A scraper on one emulator: whether it has the app, whether it can run it, and whether an install takes."""
    logs, calls, state = [], [], {"installed": installed}
    subject = ExternalScraper(logger=logs.append)

    def adb(*args, serial=None, **_kwargs):
        calls.append(args)
        out = ""
        if args[:4] == ("shell", "pm", "list", "packages"):
            out = f"package:{args[-1]}\n" if state["installed"] else ""
        elif args[:2] == ("shell", "getprop"):
            out = abis
        elif args[0] == "install":
            state["installed"] = installs
            out = "Success" if installs else "Failure [INSTALL_FAILED_NO_MATCHING_ABIS]"
        return SimpleNamespace(stdout=out, stderr="", returncode=0)

    subject._adb = adb
    return subject, logs, calls


def test_the_app_is_installed_by_itself_when_the_emulator_does_not_have_it(tmp_path):
    subject, logs, calls = installer()
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    subject._acqq_android_apk = lambda: apk
    assert subject._android_open_app(ExternalScraper.ACQQ_ANDROID_PACKAGE, "the app", serial="emulator-1") is True
    kinds = [args[0] if args[0] != "shell" else args[1] for args in calls]
    assert kinds.index("install") < kinds.index("monkey")  # installed, then opened
    assert ("install", "-r", str(apk)) in calls  # over an older copy: what was signed in to stays
    assert any("is installed" in line for line in logs)


def test_an_emulator_that_already_has_the_app_is_not_installed_to_again():
    subject, _logs, calls = installer(installed=True)
    subject._acqq_android_apk = lambda: (_ for _ in ()).throw(AssertionError("nothing should be downloaded"))
    assert subject._android_open_app(ExternalScraper.ACQQ_ANDROID_PACKAGE, "the app", serial="emulator-1") is True
    assert not any(args[0] == "install" for args in calls)


def test_an_emulator_without_arm_support_is_told_so_and_nothing_is_downloaded():
    subject, logs, calls = installer(abis="x86_64")
    subject._acqq_android_apk = lambda: (_ for _ in ()).throw(AssertionError("nothing should be downloaded"))
    assert subject._android_open_app(ExternalScraper.ACQQ_ANDROID_PACKAGE, "the app", serial="emulator-1") is False
    assert any("no ARM support" in line for line in logs)
    assert not any(args[:2] == ("shell", "monkey") for args in calls)


def test_a_failed_install_is_reported_and_the_file_is_fetched_again_next_time(tmp_path):
    subject, logs, calls = installer(installs=False)
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    subject._acqq_android_apk = lambda: apk
    assert subject._android_open_app(ExternalScraper.ACQQ_ANDROID_PACKAGE, "the app", serial="emulator-1") is False
    assert any("did not install" in line and "NO_MATCHING_ABIS" in line for line in logs)
    assert not apk.exists()
    assert not any(args[:2] == ("shell", "monkey") for args in calls)


def test_another_app_is_only_opened_never_installed():
    subject, _logs, calls = installer(installed=True)
    subject._android_install_acqq = lambda _serial: (_ for _ in ()).throw(AssertionError("not this app"))
    assert subject._android_open_app("com.example.other", "Other", serial="emulator-1") is True
    assert any(args[:2] == ("shell", "monkey") for args in calls)


def test_a_download_sent_to_another_site_is_refused(tmp_path, monkeypatch):
    import requests

    from noveldb.core import paths

    class Answer:
        url = "https://downloads.example.net/app.apk"
        headers = {"Content-Length": "20000000"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, _size):
            raise AssertionError("nothing should be read from another site")

    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: Answer())
    logs = []
    subject = ExternalScraper(logger=logs.append)
    assert subject._acqq_android_apk() is None
    assert any("another site" in line for line in logs)
    assert not list(tmp_path.rglob("*.apk"))
