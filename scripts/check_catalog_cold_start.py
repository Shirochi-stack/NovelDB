"""Measure fresh desktop processes or force-stopped Android app launches.

Search is entered as soon as the catalogue script is reachable. No preload,
Top list or saved-search readiness wait is used.
"""

import argparse
import json
import os
import re
import socket
from pathlib import Path
import subprocess
import sys
import time

import requests
import websocket

from scripts.source_names import dumps
from scripts.check_catalog_instant import DevTools

QUERY = "mesugaki"
EXPECTED = "Mesugaki Tank Enters The Academy"
SEARCH = """(()=>{
    const ready=!!window.NovelDBInstant?.ready()||performance.getEntriesByName('noveldb-search-ready').length>0;
    const initial=document.querySelectorAll('#grid .card').length;
    const started=performance.now();
    const query=document.getElementById('query');
    query.value='mesugaki';query.dispatchEvent(new Event('input',{bubbles:true}));
    const titles=Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent);
    const nav=performance.getEntriesByType('navigation')[0];
    const readyAt=performance.getEntriesByName('noveldb-search-ready')[0]?.startTime;
    const paint=performance.getEntriesByName('first-contentful-paint')[0];
    return {ready,initial,first_ms:performance.now()-started,titles,
            page_ready_ms:readyAt||nav?.domContentLoadedEventEnd||performance.now(),
            first_paint_ms:paint?.startTime||null,
            ready_before_first_paint:!paint||readyAt<=paint.startTime};
})()"""


def desktop_child(output):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    from PySide6.QtCore import QTimer
    from noveldb.qt import main_window
    from noveldb.qt.catalog.web import WORLD

    original = main_window.MainWindow
    started = float(os.environ["NOVELDB_COLD_STARTED"])

    class ObservedWindow(original):
        def __init__(self, context):
            super().__init__(context)
            self.busy = False
            self.probe = QTimer(self)
            self.probe.timeout.connect(self.poll)
            self.probe.start(10)
            QTimer.singleShot(
                30000, lambda: self.finish({"error": "Cold launch timed out"})
            )

        def finish(self, result):
            result["launch_ms"] = (time.monotonic() - started) * 1000
            Path(output).write_text(dumps(result, indent=2), encoding="utf-8")
            os._exit(
                0
                if result.get("ready")
                and result.get("first_ms", 1000) < 60
                and result.get("titles", [None])[0] == EXPECTED
                else 1
            )

        def poll(self):
            page = getattr(self.catalog, "page", None)
            if self.busy or page is None:
                return
            self.busy = True
            page.runJavaScript("!!window.NovelDBGeneric", WORLD, self.reachable)

        def reachable(self, ready):
            self.busy = False
            if not ready:
                return
            self.probe.stop()
            self.catalog.page.runJavaScript(
                "JSON.stringify(" + SEARCH + ")",
                WORLD,
                lambda value: self.finish(json.loads(value)),
            )

    main_window.MainWindow = ObservedWindow
    from noveldb.qt.app import main

    raise SystemExit(main(["--page", "catalog"]))


def desktop_package(args, number):
    profile = args.output.with_name(
        args.output.stem + "-package-" + str(number)
    ).resolve()
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    env = dict(
        os.environ,
        NOVELDB_HOME=str(profile),
        QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox",
    )
    started = time.monotonic()
    process = subprocess.Popen(
        [str(args.desktop_executable.resolve()), "--page", "catalog"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    tools = None
    try:
        deadline = started + 60
        while time.monotonic() < deadline:
            try:
                tools = DevTools(
                    f"http://127.0.0.1:{port}", url_contains="/generic/index.html"
                )
                if tools.evaluate(
                    "performance.getEntriesByName('noveldb-search-ready').length>0"
                ):
                    result = tools.evaluate(SEARCH)
                    result["launch_to_probe_ms"] = (time.monotonic() - started) * 1000
                    result["executable"] = args.desktop_executable.name
                    return result
                tools.socket.close()
                tools = None
            except (
                StopIteration,
                RuntimeError,
                OSError,
                ValueError,
                requests.RequestException,
                websocket.WebSocketException,
            ):
                if tools:
                    tools.socket.close()
                    tools = None
            time.sleep(0.02)
        raise AssertionError("Packaged catalogue unavailable after a cold launch")
    finally:
        if tools:
            tools.socket.close()
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True
        )


def android(args):
    adb = [args.adb, "-s", args.serial]
    subprocess.run(
        adb + ["shell", "am", "force-stop", args.package],
        check=True,
        capture_output=True,
    )
    started = time.monotonic()
    launch = subprocess.run(
        adb + ["shell", "am", "start", "-W", "-n", args.package + "/" + args.activity],
        check=True,
        capture_output=True,
        text=True,
    )
    deadline = started + 30
    tools = None
    while time.monotonic() < deadline:
        try:
            pid = subprocess.check_output(
                adb + ["shell", "pidof", args.package], text=True
            ).strip()
            if not pid:
                continue
            subprocess.run(
                adb
                + [
                    "forward",
                    "tcp:9223",
                    "localabstract:webview_devtools_remote_" + pid,
                ],
                check=True,
                capture_output=True,
            )
            tools = DevTools("http://127.0.0.1:9223")
            if tools.evaluate("!!window.NovelDBGeneric"):
                break
            tools.socket.close()
            tools = None
        except (
            StopIteration,
            RuntimeError,
            OSError,
            ValueError,
            subprocess.CalledProcessError,
            requests.RequestException,
            websocket.WebSocketException,
        ):
            if tools:
                tools.socket.close()
                tools = None
        time.sleep(0.02)
    if tools is None:
        raise AssertionError("Catalogue unavailable after a cold launch")
    try:
        result = tools.evaluate(SEARCH)
        result["launch_to_probe_ms"] = (time.monotonic() - started) * 1000
        result["process_id"] = int(pid)
        display = re.search(r"TotalTime: (\d+)", launch.stdout)
        result["activity_display_ms"] = int(display.group(1)) if display else None
        # The next process must start at the catalogue, without restoring this test query.
        tools.evaluate(
            "document.getElementById('query').value='';document.getElementById('query').dispatchEvent(new Event('input',{bubbles:true}))"
        )
        return result
    finally:
        tools.socket.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["desktop", "android"], default="desktop")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--desktop-child")
    parser.add_argument("--desktop-executable", type=Path)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", default="emulator-5560")
    parser.add_argument("--package", default="com.shirochi.npiamobile")
    parser.add_argument("--activity", default="com.shirochi.npia.app.MainActivity")
    args = parser.parse_args()
    if args.desktop_child:
        desktop_child(args.desktop_child)
        return
    if args.repeat < 1:
        parser.error("Use a positive repeat count")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for number in range(args.repeat):
        if args.platform == "desktop" and args.desktop_executable:
            result = desktop_package(args, number)
        elif args.platform == "desktop":
            run = args.output.with_name(args.output.stem + "-" + str(number) + ".json")
            run.unlink(missing_ok=True)
            env = dict(
                os.environ,
                NOVELDB_HOME=str(run.with_suffix("").resolve()),
                NOVELDB_COLD_STARTED=str(time.monotonic()),
            )
            command = [
                sys.executable,
                "-m",
                "scripts.check_catalog_cold_start",
                "--output",
                str(args.output),
                "--desktop-child",
                str(run),
            ]
            completed = subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
            )
            if not run.exists() or completed.returncode != 0:
                raise AssertionError(
                    completed.stderr[-3000:]
                    + (run.read_text(encoding="utf-8") if run.exists() else "")
                )
            result = json.loads(run.read_text(encoding="utf-8"))
        else:
            result = android(args)
        results.append(result)
        args.output.write_text(
            dumps(
                {"platform": args.platform, "fresh_process": True, "results": results},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(dumps(result), flush=True)
        assert result.get("ready"), result
        assert result.get("ready_before_first_paint"), result
        assert result.get("first_ms", 1000) < 60, result
        assert result.get("titles", [None])[0] == EXPECTED, result


if __name__ == "__main__":
    main()
