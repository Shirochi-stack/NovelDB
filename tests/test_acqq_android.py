"""The Tools menu's "Open the Android emulator (ac.qq comics app)".

It acts on NovelDB's own emulator only. Another project's emulator that happens to be connected is never opened,
installed to or asked what it has: the first version installed the app on one (it waited for "a device", found
the other project's already up, and fell back to it).
"""

from types import SimpleNamespace

import pytest

from external_scraper import ExternalScraper
from noveldb.qt import tools

PACKAGE = ExternalScraper.ACQQ_ANDROID_PACKAGE
OTHER, OWN = ("emulator-5554", "glossarion"), ("emulator-5556", "noveldb_shots_api35")


def scraper(devices, installed=(), abis="x86_64,arm64-v8a", installs=True, booted=True):
    """A scraper whose connected emulators are `devices` ([(serial, name)]); the comics app is on those in
    `installed`. Every adb call is recorded with the emulator it was sent to."""
    logs, calls, state = [], [], {"installed": set(installed)}
    subject = ExternalScraper(logger=logs.append)
    # Which emulators run is read from this computer's processes; asking the emulators themselves is the bug.
    subject._android_running_avds = lambda: {name: serial for serial, name in devices}
    subject._android_devices = lambda: pytest.fail("the emulators were asked who they are")
    subject._android_serial_for_avd = lambda _name: pytest.fail("the emulators were asked who they are")

    def adb(*args, serial=None, **_kwargs):
        calls.append((serial, args))
        out = ""
        if args[:4] == ("shell", "pm", "list", "packages"):
            out = f"package:{args[-1]}\n" if serial in state["installed"] else ""
        elif args[:3] == ("shell", "getprop", "ro.product.cpu.abilist"):
            out = abis
        elif args[:3] == ("shell", "getprop", "sys.boot_completed"):
            out = "1" if booted else ""
        elif args[0] == "install":
            if installs:
                state["installed"].add(serial)
            out = "Success" if installs else "Failure [INSTALL_FAILED_NO_MATCHING_ABIS]"
        return SimpleNamespace(stdout=out, stderr="", returncode=0)

    subject._adb = adb
    return subject, logs, calls


def sent_to(calls, serial):
    return [args for target, args in calls if target == serial]


def test_the_menu_has_the_entry_beside_the_sfc_ones():
    names = [name for name, _label in tools.TOOLS]
    assert "acqq_android_open" in names
    assert names.index("acqq_android_open") > names.index("sfc_copy")
    assert dict(tools.TOOLS)["acqq_android_open"] == "Open the Android emulator (ac.qq comics app)"
    assert hasattr(tools.Tools, "_acqq_android_open")


@pytest.mark.parametrize("name,own", [("glossarion", False), ("syfe_poc_api35", False), ("catalog_api35", False),
                                       ("sfacg_play_api35", False), ("noveldb_shots_api35", True),
                                       ("Npia_Android", True), ("Npia_Play_Store", True), ("", False)])
def test_only_noveldbs_own_emulators_count(name, own):
    assert ExternalScraper._noveldb_avd(name) is own


def test_another_projects_emulator_is_left_alone_even_when_it_has_the_app(tmp_path):
    # What went wrong: the other project's emulator was connected, and had the app.
    subject, logs, calls = scraper([OTHER], installed={OTHER[0]})
    opened = []
    subject.open_android_emulator = lambda **kwargs: opened.append(kwargs) or True
    subject._acqq_android_apk = lambda: (_ for _ in ()).throw(AssertionError("nothing should be downloaded"))
    assert subject.open_android_emulator_for_acqq() is True
    assert opened == [{"package": PACKAGE, "label": ExternalScraper.ACQQ_ANDROID_LABEL}]  # NovelDB's own is started
    assert calls == []  # not one command went to the other project's emulator
    assert not any("glossarion" in line for line in logs)


def test_a_running_noveldb_emulator_is_used_and_the_app_installed_there_when_missing(tmp_path):
    subject, logs, calls = scraper([OTHER, OWN])
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    subject._acqq_android_apk = lambda: apk
    subject.open_android_emulator = lambda **_kwargs: pytest.fail("no other emulator should be started")
    assert subject.open_android_emulator_for_acqq() is True
    assert sent_to(calls, OTHER[0]) == []
    own = sent_to(calls, OWN[0])
    kinds = [args[0] if args[0] != "shell" else args[1] for args in own]
    assert kinds.index("install") < kinds.index("monkey")  # installed, then opened
    assert ("install", "-r", str(apk)) in own  # over an older copy: what was signed in to stays
    assert all(target == OWN[0] for target, _args in calls)
    assert any("noveldb_shots_api35 is running" in line for line in logs)


def test_a_running_noveldb_emulator_that_has_the_app_is_only_opened():
    subject, _logs, calls = scraper([OTHER, OWN], installed={OWN[0]})
    subject._acqq_android_apk = lambda: (_ for _ in ()).throw(AssertionError("nothing should be downloaded"))
    assert subject.open_android_emulator_for_acqq() is True
    assert not any(args[0] == "install" for _target, args in calls)
    assert [target for target, args in calls if args[:2] == ("shell", "monkey")] == [OWN[0]]


def test_without_a_named_emulator_nothing_is_opened_or_installed():
    # adb with no serial picks any connected device: for this app that is refused outright.
    subject, logs, calls = scraper([OTHER])
    subject._acqq_android_apk = lambda: (_ for _ in ()).throw(AssertionError("nothing should be downloaded"))
    assert subject._android_open_app(PACKAGE, "the app", serial=None) is False
    assert subject._android_install_acqq(None) is False
    assert calls == []
    assert any("No NovelDB emulator" in line for line in logs)


def test_after_starting_it_waits_for_noveldbs_emulator_not_for_any_device(monkeypatch):
    # The other project's emulator is up; NovelDB's never connects. Nothing may happen anywhere.
    subject, logs, calls = scraper([OTHER], installed={OTHER[0]})
    waited = []
    monkeypatch.setattr(subject, "_android_wait_for_avd", lambda name, timeout=300: waited.append(name) or "")
    subject._android_wait_for_device = lambda **_kwargs: pytest.fail("any connected device is not what is waited for")
    started = []
    monkeypatch.setattr("external_scraper.threading.Thread",
                        lambda target, daemon=True: SimpleNamespace(start=lambda: started.append(target) or target()))
    subject._launch_sfc_after_android_boot_async("noveldb_shots_api35", package=PACKAGE, label="the app")
    assert waited == ["noveldb_shots_api35"] and started
    assert calls == []
    assert any("did not come up" in line for line in logs)


def test_waiting_for_an_emulator_answers_only_for_the_one_named(monkeypatch):
    monkeypatch.setattr("external_scraper.time.sleep", lambda _seconds: None)
    subject, _logs, calls = scraper([OTHER, OWN])
    assert subject._android_wait_for_avd("noveldb_shots_api35", timeout=5) == OWN[0]
    assert all(target == OWN[0] for target, _args in calls)
    assert subject._android_wait_for_avd("Npia_Android", timeout=0.01) == ""  # not connected: no other one will do
    assert subject._android_wait_for_avd("", timeout=5) == ""


def test_an_emulator_without_arm_support_is_told_so_and_nothing_is_downloaded():
    subject, logs, calls = scraper([OWN], abis="x86_64")
    subject._acqq_android_apk = lambda: (_ for _ in ()).throw(AssertionError("nothing should be downloaded"))
    assert subject._android_open_app(PACKAGE, "the app", serial=OWN[0]) is False
    assert any("no ARM support" in line for line in logs)
    assert not any(args[:2] == ("shell", "monkey") for _target, args in calls)


def test_a_failed_install_is_reported_and_the_file_is_fetched_again_next_time(tmp_path):
    subject, logs, calls = scraper([OWN], installs=False)
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    subject._acqq_android_apk = lambda: apk
    assert subject._android_open_app(PACKAGE, "the app", serial=OWN[0]) is False
    assert any("did not install" in line and "NO_MATCHING_ABIS" in line for line in logs)
    assert not apk.exists()
    assert not any(args[:2] == ("shell", "monkey") for _target, args in calls)


def test_another_app_is_only_opened_never_installed():
    subject, _logs, calls = scraper([OWN], installed={OWN[0]})
    subject._android_install_acqq = lambda _serial: pytest.fail("not this app")
    assert subject._android_open_app("com.example.other", "Other", serial=OWN[0]) is True
    assert any(args[:2] == ("shell", "monkey") for _target, args in calls)


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


def test_settings_are_read_with_or_without_spaces_around_the_equals_sign(tmp_path, monkeypatch):
    # NovelDB's own emulator writes "key = value": read as missing, it was passed over for another one.
    sdk = tmp_path / "sdk"
    image = sdk / "system-images" / "android-35" / "google_apis" / "x86_64"
    image.mkdir(parents=True)
    (image / "system.img").write_bytes(b"")
    for name, line in (("spaced", "image.sysdir.1 = system-images\\android-35\\google_apis\\x86_64\\"),
                       ("plain", "image.sysdir.1=system-images\\android-35\\google_apis\\x86_64\\"),
                       ("missing", "image.sysdir.1 = system-images\\android-99\\none\\x86_64\\"),
                       ("empty", "abi.type = x86_64")):
        folder = tmp_path / f"{name}.avd"
        folder.mkdir()
        (folder / "config.ini").write_text("abi.type = x86_64\n" + line + "\ntag.id = google_apis\n", encoding="utf-8")
    monkeypatch.setattr(ExternalScraper, "_android_avd_dir", classmethod(lambda cls, name: str(tmp_path / f"{name}.avd")))
    monkeypatch.setattr(ExternalScraper, "_android_sdk_dir", staticmethod(lambda: str(sdk)))
    assert ExternalScraper._android_avd_system_image_ok("spaced") is True
    assert ExternalScraper._android_avd_system_image_ok("plain") is True
    assert ExternalScraper._android_avd_system_image_ok("missing") is False
    assert ExternalScraper._android_avd_system_image_ok("empty") is False
    assert ExternalScraper._avd_system_image_package("spaced") == "system-images;android-35;google_apis;x86_64"
    assert ExternalScraper._avd_config_value("spaced", "tag.id") == "google_apis"


@pytest.mark.parametrize("method", ["connections", "net_connections"])
def test_running_emulators_are_read_from_their_processes_with_either_version_of_the_library(monkeypatch, method):
    # The first version called the newer name only: with the older library it found no emulator at all.
    import sys

    def listening(*ports):
        return [SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=port)) for port in ports]

    def process(name, arguments, ports):
        made = SimpleNamespace(info={"name": name, "cmdline": arguments})
        setattr(made, method, lambda kind="tcp": listening(*ports))
        return made

    processes = [
        process("emulator.exe", ["emulator.exe", "-avd", "glossarion"], []),  # the launcher: no port of its own
        process("qemu-system-x86_64-headless.exe", ["qemu", "-avd", "glossarion", "-memory", "4096"], [5554, 5555, 8554]),
        process("qemu-system-x86_64.exe", ["qemu", "@noveldb_shots_api35"], [5557, 5556, 53641]),
        process("chrome.exe", ["chrome.exe", "-avd", "not_an_emulator"], [5560]),
        process("qemu-system-x86_64.exe", ["qemu", "-avd", "starting_up"], []),  # not listening yet
    ]
    fake = SimpleNamespace(process_iter=lambda _fields: iter(processes), CONN_LISTEN="LISTEN")
    monkeypatch.setitem(sys.modules, "psutil", fake)
    assert ExternalScraper._android_running_avds() == {"glossarion": "emulator-5554", "noveldb_shots_api35": "emulator-5556"}
