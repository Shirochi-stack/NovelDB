"""Everything the window needs: settings, the npia session, the queue, the browser worker, uploads."""

from __future__ import annotations

import threading
import tempfile
from pathlib import Path
from dataclasses import replace
from time import perf_counter
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from noveldb.core import net_timeouts, paths
from noveldb.core.catalog.cart import LinkCart
from noveldb.core.external.browser_worker import BrowserWorker
from noveldb.core.jobs import JobDispatcher
from noveldb.core.events import CancelToken
from noveldb.core.queue.model import Job
from noveldb.core.queue.runner import QueueRunner
from noveldb.core.settings.model import AppSettings
from noveldb.core.settings.store import ConfigStore
from noveldb.core.sources import detect_source, engine_for, split_inputs
from noveldb.core.upload import UploadContext, UploadHistory, links_text, upload_items
from noveldb.qt.bridge import QtEventHub, QtJobEvents, QtPrompts


class RunnerJobEvents(QtJobEvents):
    """Job events that also keep the queue's `Job` (progress, status, title) current."""

    def __init__(self, hub: QtEventHub, runner: QueueRunner, job: Job, live=None) -> None:
        super().__init__(hub, job.id)
        self.runner = runner
        self.job = job
        self.live = live

    def live_book(self, chapters):
        if self.live is not None:
            self.live.book(chapters)

    def live_chapter(self, index, html, images=(), error=""):
        if self.live is not None:
            self.live.chapter(index, html, images, error)

    def status(self, text):
        super().status(text)
        self.runner.update(self.job, status=text)

    def progress(self, value, total, info):
        super().progress(value, total, info)
        self.runner.update(self.job, value=int(value), total=int(total), info=info)

    def book_info(self, info):
        super().book_info(info)
        title = (info or {}).get("title")
        if title and not self.job.title:
            self.runner.update(self.job, title=str(title))


class AppContext(QObject):
    settingsChanged = Signal()
    signedInChanged = Signal(bool)
    googleDriveChanged = Signal()
    _googleDriveFinished = Signal(str, str)
    _uploadLinksReady = Signal(str)
    cartChanged = Signal()
    libraryChanged = Signal()
    _libraryBooks = Signal(object)
    toolRequested = Signal(str)  # e.g. "tag_search", handled by the main window
    engineUpdateChecked = Signal(object)  # an engine_updates.UpdateResult
    appUpdateChecked = Signal(object, bool)  # an app_update.CheckResult, and whether it was automatic

    def __init__(self, store: ConfigStore | None = None, start_workers: bool = True) -> None:
        super().__init__()
        self.store = store or ConfigStore()
        self.store.ensure_exists()
        self.settings: AppSettings = self.store.load()
        from noveldb.core.upload.google_drive import GoogleDriveAuth

        self.google_drive = GoogleDriveAuth(lambda: self.settings.secrets.google_drive_refresh_token)
        self.google_drive_busy = False
        self.google_drive_error = ""
        self._googleDriveFinished.connect(self._finish_google_drive)
        self._uploadLinksReady.connect(self._copy_upload_links)
        net_timeouts.configure_from(self.settings)
        self.hub = QtEventHub()
        from noveldb.core.library_live import LibraryLive
        self.library_live = LibraryLive()
        self._live_jobs = {}
        self.prompts = QtPrompts()
        self.signed_in = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save_now)

        from npia_auth import NpiaAuth

        self.auth = NpiaAuth()
        self.browser = BrowserWorker(log=lambda text: self.hub.log("", text)) if start_workers else None
        # The Catalog reader's own browser (noveldb.core.external.reader_scraper): reading a book never
        # waits for a download that has the downloader's browser.
        from noveldb.core.external.reader_scraper import reader_scraper

        self.reader_browser = BrowserWorker(scraper_factory=reader_scraper, log=lambda text: self.hub.log("", text)) \
            if start_workers else None
        data = paths.data_dir()
        self.uploads = UploadHistory(str(data / "upload-history.json"))
        self.cart = LinkCart(str(data / "link-cart.json"), on_change=self.cartChanged.emit)
        self._download = JobDispatcher(lambda: self.settings, self.auth, self.browser, self._events_for, self.prompts)
        self.runner = QueueRunner(
            self._execute_job,
            persist_path=data / "queue.json",
            max_concurrent=self.settings.queue.max_concurrent_jobs,
            on_change=lambda job: self.hub.jobChanged.emit(job.id),
        )
        from noveldb.core.library_updates import LibraryUpdates
        self.library_updates = LibraryUpdates(data / "library-updates.json")
        self._libraryBooks.connect(self._queue_library_books)
        self._library_timer = QTimer(self, interval=15 * 60 * 1000)
        self._library_timer.timeout.connect(self.refresh_library_background)
        self._library_timer.start()
        QTimer.singleShot(2000, self.refresh_library_background)

    # -- settings --------------------------------------------------------------------

    def update_settings(self, change: Callable[[AppSettings], AppSettings]) -> None:
        updated = change(self.settings)
        if updated == self.settings:
            return
        self.settings = updated
        net_timeouts.configure_from(updated)
        self.runner.set_max_concurrent(updated.queue.max_concurrent_jobs)
        self.settingsChanged.emit()
        self._save_timer.start()

    def save_now(self) -> None:
        self._save_timer.stop()
        try:
            self.store.save(self.settings)
        except OSError as exc:
            self.hub.message.emit(f"Could not save settings: {exc}")

    # -- npia session ---------------------------------------------------------------------

    def sign_in(self, on_done: Callable[[bool, str], None] | None = None) -> None:
        """Sign in with the saved email/password (or the saved key) on a worker thread.

        Saved credentials are always used, not only with auto-login - the same fix as the mobile
        apps, where saving an account without auto-login left downloads anonymous.
        """
        secrets = self.settings.secrets

        def work():
            ok, message = False, ""
            if secrets.email and secrets.password:
                ok = self.auth.login(secrets.email, secrets.password)
                message = "Signed in to npia." if ok else "npia sign-in failed; check the email and password."
                if ok:
                    self.update_settings_from_thread(lambda s: replace(s, secrets=replace(s.secrets, loginkey=self.auth.loginkey)))
            if not ok and secrets.loginkey:
                self.auth.set_manual_key(secrets.loginkey)
                ok, message = True, message or "Using the saved npia session key."
            if not message:
                message = "npia: no saved login; only free chapters can be read."
            self.signed_in = ok
            self.hub.log("", message)
            self.hub.message.emit(message)
            self.signedInChanged.emit(ok)
            if on_done:
                on_done(ok, message)

        threading.Thread(target=work, name="npia-sign-in", daemon=True).start()

    def check_engine_updates(self, automatic: bool = False) -> None:
        """Look for rule-engine updates on a worker thread.

        `automatic`: the daily check, only for the engines the user opted in to, logging only news
        and failures. Otherwise (the Settings button) every engine is checked.
        """
        from noveldb.core import engine_updates

        engines = [e for e in engine_updates.ENGINES
                   if not automatic or self.settings.engines.auto_update(e.name)]

        def work():
            for engine in engines:
                try:
                    if automatic:
                        result = engine_updates.check_if_due(engine, lambda text: self.hub.log("", text))
                    else:
                        result = engine_updates.check_for_update(engine)
                        self.hub.log("", result.message)
                except Exception as exc:  # never let a check take the app down
                    result = engine_updates.UpdateResult(engine, "failed", f"{engine.title} update check failed: {exc}")
                    self.hub.log("", result.message)
                if result is not None:
                    self.engineUpdateChecked.emit(result)

        if engines:
            threading.Thread(target=work, name="engine-updates", daemon=True).start()

    def check_app_update(self, automatic: bool = False) -> None:
        """Look for a newer build of the app on a worker thread.

        `automatic`: the daily check, only when the user leaves it on; it reports nothing unless an
        update turns up. Otherwise (the Settings button) every result is reported.
        """
        from noveldb.core import app_update

        if automatic and not self.settings.updates.check_automatically:
            return

        def work():
            try:
                result = app_update.check_if_due() if automatic else app_update.check()
            except Exception as exc:  # never let a check take the app down
                result = app_update.CheckResult("failed", f"Could not check for app updates: {exc}")
            if result is None:
                return
            if not automatic or result.status == "update":
                self.hub.log("", result.message)
            self.appUpdateChecked.emit(result, automatic)

        threading.Thread(target=work, name="app-update", daemon=True).start()

    def update_settings_from_thread(self, change: Callable[[AppSettings], AppSettings]) -> None:
        QTimer.singleShot(0, self, lambda: self.update_settings(change))

    # -- queue ---------------------------------------------------------------------

    def _events_for(self, job: Job) -> QtJobEvents:
        return RunnerJobEvents(self.hub, self.runner, job, self._live_jobs.get(job.id))

    def enqueue_text(self, text: str, output_dir: str = "") -> list[Job]:
        jobs = []
        for line in split_inputs(text):
            source = detect_source(line)
            if source is None:
                continue
            jobs.append(Job(line, source=source, engine=engine_for(line, source, self.settings.npia.engine),
                            output_dir=output_dir))
        return self.runner.submit(jobs)

    def _execute_job(self, job: Job, token: CancelToken) -> dict:
        started = perf_counter()
        timing = self.settings.shared.chapter_timing
        if job.library_target:
            self._live_jobs[job.id] = self.library_live.begin(job.library_target, job.input)
        try:
            return self._download_and_upload(job, token)
        finally:
            live = self._live_jobs.pop(job.id, None)
            if live is not None:
                live.finish()
            if timing:
                self.hub.log(job.id, f"Total time: {perf_counter() - started:.2f} s")

    def _download_and_upload(self, job: Job, token: CancelToken) -> dict:
        if job.library_target:
            return self._refresh_library_job(job, token)
        result = self._download(job, token) or {}
        if result.get("ok", True) and not result.get("cancelled"):
            for path in result.get("outputs") or []:
                if Path(path).suffix.lower() == ".epub":
                    self.library_updates.record(path, job.input, job.download_ranged is not False)
        upload = self.settings.upload
        if not upload.auto_upload or not result.get("ok", True) or result.get("cancelled") or token.cancelled:
            return result
        if not upload.agreement_accepted:
            self.hub.log(job.id, "Automatic upload skipped. Enable Upload finished files in Settings to accept the sharing agreement.")
            return result

        from noveldb.qt.uploads import items_for_jobs

        finished = replace(job, outputs=list(result.get("outputs") or []), title=result.get("title") or job.title)
        items = items_for_jobs([finished])
        if not items:
            return result
        events = self._events_for(job)
        host = ""

        def status(text):
            if text != job.status:
                events.status(text)

        def on_host(index, count, name):
            nonlocal host
            host = name
            events.log(f"Uploading {index + 1} of {count} to {name}")
            status(f"Uploading {index + 1} of {count} to {name}")

        def progress(index, count, sent, total):
            if total > 0:
                status(f"Uploading {index + 1} of {count} to {host} ({sent * 100 // total}%)")

        def outcome(item):
            if item.link:
                events.log(f"Uploaded {item.label}: {item.link}")
            else:
                events.log(f"Automatic upload failed for {item.label}: {item.error}")

        try:
            outcomes = upload_items(
                items, upload.hosts, self.upload_context(stopped=lambda: token.cancelled, upload_settings=upload),
                bundle=upload.zip_bundle, max_mb=upload.zip_max_mb, history=self.uploads,
                on_prepare=lambda index, count, name, done, total: status(f"Preparing {name} for upload"),
                on_host=on_host, progress=progress, on_outcome=outcome,
            )
            errors = [item.error for item in outcomes if item.error]
            links = links_text(outcomes)
            if links and self.settings.upload.copy_link:
                self._uploadLinksReady.emit(links)
            result["status"] = "Downloaded; automatic upload failed" if errors else "Done; uploaded"
        except Exception as exc:
            errors = [str(exc) or type(exc).__name__]
            events.log(f"Automatic upload failed: {errors[0]}")
            result["status"] = "Downloaded; automatic upload failed"
        result["warnings"] = list(result.get("warnings") or []) + [f"Automatic upload: {error}" for error in errors]
        return result

    def register_library_books(self, books):
        self.library_updates.discover(books, self.runner.jobs)

    def configure_library(self, **changes):
        self.library_updates.configure(**changes)
        self._cancel_disabled_library_jobs()
        self.libraryChanged.emit()
        self.refresh_library_background()

    def set_library_auto_update(self, paths, enabled):
        self.library_updates.set_books(paths, enabled)
        self._cancel_disabled_library_jobs()
        self.libraryChanged.emit()

    def _cancel_disabled_library_jobs(self):
        for job in self.runner.jobs:
            if job.library_target and job.active and not self.library_updates.allowed(job.library_target):
                self.runner.cancel(job.id)

    def request_library_update(self, path):
        self.request_library_updates([path])

    def request_library_updates(self, paths_to_update):
        state = self.library_updates
        active = [job for job in self.runner.jobs if job.active]
        targets = {job.library_target for job in active}
        manual_urls = {job.input for job in active if not job.library_target}
        added = []
        for value in paths_to_update:
            path = str(Path(value).resolve())
            if not Path(path).is_file() or not state.allowed(path) or path in targets:
                continue
            url = state.entry(path)["url"]
            if url in manual_urls:
                continue
            source = detect_source(url)
            if source is not None:
                added.append(Job(url, source=source, engine=engine_for(url, source, self.settings.npia.engine),
                                 title=Path(path).stem, library_target=path))
                targets.add(path)
        if added:
            self.runner.submit(added)

    def refresh_library_background(self):
        if not (self.library_updates.data["enabled"] and self.library_updates.data["background"]):
            return
        from noveldb.core.library import scan
        folder = Path(self.settings.output.output_dir or paths.default_output_dir())
        threading.Thread(target=lambda: self._libraryBooks.emit(scan(folder)), daemon=True).start()

    def _queue_library_books(self, books):
        self.register_library_books(books)
        if self.library_updates.data["enabled"] and self.library_updates.data["background"]:
            self.request_library_updates([file.path for book in books for file in book.files if file.format == "EPUB"])

    def _refresh_library_job(self, job, token):
        from noveldb.core.library_updates import replace_epub
        target = Path(job.library_target)
        if not self.library_updates.allowed(target):
            return {"cancelled": True}
        with tempfile.TemporaryDirectory(prefix=".library-update-", dir=target.parent) as staging:
            result = self._download(job, token, output_dir=staging) or {}
            candidates = [Path(p) for p in result.get("outputs", []) if Path(p).suffix.lower() == ".epub"]
            if token.cancelled or result.get("cancelled") or not self.library_updates.allowed(target):
                return {"cancelled": True}
            if not result.get("ok", True) or result.get("warnings") or result.get("incomplete") or len(candidates) != 1:
                raise ValueError("Library refresh did not produce a complete EPUB. See the download log.")
            spine_count = replace_epub(candidates[0], target)
        result.update(outputs=[str(target)], status="Library updated")
        live = self._live_jobs.get(job.id)
        if live is not None:
            live.finish(saved=True, spine_count=spine_count)
        self.hub.jobSaved.emit(job.id, str(target))
        if self.library_updates.data["uploadDrive"]:
            from noveldb.core.upload.google_drive import GoogleDriveUploader
            host = GoogleDriveUploader()
            host.check(str(target))
            saved = host.upload(str(target), self.upload_context(stopped=lambda: token.cancelled))
            self.uploads.add(saved)
            self.hub.log(job.id, f"Library uploaded to Google Drive: {saved.link}")
        return result

    def _copy_upload_links(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.hub.message.emit("Upload links copied to the clipboard.")

    def upload_context(self, stopped=lambda: False, upload_settings=None) -> UploadContext:
        s = self.settings.secrets
        upload = upload_settings or self.settings.upload
        return UploadContext(pixeldrain_key=s.pixeldrain_key, gofile_token=s.gofile_token,
                             catbox_userhash=s.catbox_userhash, stopped=stopped,
                             google_drive_folder_id=upload.google_drive_folder_id,
                             google_access_token=self.google_drive.access_token if self.google_drive.signed_in else None)

    def sign_in_google_drive(self) -> None:
        if self.google_drive_busy:
            return
        self.google_drive_busy, self.google_drive_error = True, ""
        self.googleDriveChanged.emit()

        def work():
            try:
                refresh = self.google_drive.sign_in()
                self._googleDriveFinished.emit(refresh, "")
            except Exception as exc:
                self._googleDriveFinished.emit("", str(exc) or "Google sign-in did not complete.")

        threading.Thread(target=work, name="google-drive-sign-in", daemon=True).start()

    def _finish_google_drive(self, refresh: str, error: str) -> None:
        self.google_drive_busy, self.google_drive_error = False, error
        if refresh:
            self.update_settings(lambda s: replace(s, secrets=replace(s.secrets, google_drive_refresh_token=refresh)))
            self.save_now()
            self.hub.message.emit("Signed in to Google Drive.")
        self.googleDriveChanged.emit()

    def disconnect_google_drive(self) -> None:
        if self.google_drive_busy:
            return
        self.update_settings(lambda s: replace(s, secrets=replace(s.secrets, google_drive_refresh_token="")))
        self.save_now()
        self.google_drive_error = ""
        self.googleDriveChanged.emit()

    def shutdown(self) -> None:
        self.runner.cancel_all()
        self.runner.wait_idle(5)
        if self.browser is not None:
            self.browser.shutdown()
        if self.reader_browser is not None:
            self.reader_browser.shutdown()
        self.save_now()
        self.library_live.close()
