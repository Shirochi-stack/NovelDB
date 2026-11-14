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
