"""Everything the window needs: settings, the npia session, the queue, the browser worker, uploads."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from noveldb.core import net_timeouts, paths
from noveldb.core.catalog.cart import LinkCart
from noveldb.core.external.browser_worker import BrowserWorker
from noveldb.core.jobs import JobDispatcher
from noveldb.core.queue.model import Job
from noveldb.core.queue.runner import QueueRunner
from noveldb.core.settings.model import AppSettings
from noveldb.core.settings.store import ConfigStore
from noveldb.core.sources import detect_source, engine_for, split_inputs
from noveldb.core.upload import UploadContext, UploadHistory
from noveldb.qt.bridge import QtEventHub, QtJobEvents, QtPrompts


class RunnerJobEvents(QtJobEvents):
    """Job events that also keep the queue's `Job` (progress, status, title) current."""

    def __init__(self, hub: QtEventHub, runner: QueueRunner, job: Job) -> None:
        super().__init__(hub, job.id)
        self.runner = runner
        self.job = job

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
    cartChanged = Signal()
    toolRequested = Signal(str)  # e.g. "tag_search", handled by the main window
    engineUpdateChecked = Signal(object)  # an engine_updates.UpdateResult
    appUpdateChecked = Signal(object, bool)  # an app_update.CheckResult, and whether it was automatic

    def __init__(self, store: ConfigStore | None = None, start_workers: bool = True) -> None:
        super().__init__()
        self.store = store or ConfigStore()
        self.store.ensure_exists()
        self.settings: AppSettings = self.store.load()
        net_timeouts.configure_from(self.settings)
        self.hub = QtEventHub()
        self.prompts = QtPrompts()
        self.signed_in = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save_now)

        from npia_auth import NpiaAuth

        self.auth = NpiaAuth()
        self.browser = BrowserWorker(log=lambda text: self.hub.log("", text)) if start_workers else None
        data = paths.data_dir()
        self.uploads = UploadHistory(str(data / "upload-history.json"))
        self.cart = LinkCart(str(data / "link-cart.json"), on_change=self.cartChanged.emit)
        self.runner = QueueRunner(
            JobDispatcher(lambda: self.settings, self.auth, self.browser, self._events_for, self.prompts),
            persist_path=data / "queue.json",
            max_concurrent=self.settings.queue.max_concurrent_jobs,
            on_change=lambda job: self.hub.jobChanged.emit(job.id),
        )

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
        return RunnerJobEvents(self.hub, self.runner, job)

    def enqueue_text(self, text: str, output_dir: str = "") -> list[Job]:
        jobs = []
        for line in split_inputs(text):
            source = detect_source(line)
            if source is None:
                continue
            jobs.append(Job(line, source=source, engine=engine_for(line, source, self.settings.npia.engine),
                            output_dir=output_dir))
        return self.runner.submit(jobs)

    def upload_context(self, stopped=lambda: False) -> UploadContext:
        s = self.settings.secrets
        return UploadContext(pixeldrain_key=s.pixeldrain_key, gofile_token=s.gofile_token,
                             catbox_userhash=s.catbox_userhash, stopped=stopped)

    def shutdown(self) -> None:
        self.runner.cancel_all()
        self.runner.wait_idle(5)
        if self.browser is not None:
            self.browser.shutdown()
        self.save_now()
