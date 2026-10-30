package com.shirochi.npia.app

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import com.shirochi.npia.app.model.ConfigTransfer
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.app.model.JsonFileStore
import com.shirochi.npia.app.model.LogEntry
import com.shirochi.npia.app.model.LogLines
import com.shirochi.npia.app.model.QueueLog
import com.shirochi.npia.app.model.RunSummary
import com.shirochi.npia.app.model.UploadBatch
import com.shirochi.npia.app.model.UploadNaming
import com.shirochi.npia.app.model.UploadReport
import com.shirochi.npia.app.model.UploadState
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SettingsCoding
import com.shirochi.npia.core.SharedCookies
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.UploadSettings
import com.shirochi.npia.images.AndroidImageProcessor
import com.shirochi.npia.pipeline.DownloadPipeline
import com.shirochi.npia.pipeline.JobOutcome
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.sources.engines.EngineFetch
import com.shirochi.npia.sources.engines.RuleEngine
import com.shirochi.npia.sources.engines.RuleEngines
import com.shirochi.npia.sources.engines.UpdateResult
import com.shirochi.npia.sources.npia.NpiaAuth
import com.shirochi.npia.sources.npia.NpiaCookieSetting
import com.shirochi.npia.sources.session.HardSessions
import com.shirochi.npia.sources.session.SessionSite
import com.shirochi.npia.sources.session.SessionStatus
import com.shirochi.npia.sources.session.SiteSessions
import com.shirochi.npia.sources.sfc.SfcAdapter
import com.shirochi.npia.upload.LocalTestUploader
import com.shirochi.npia.upload.PlannedUpload
import com.shirochi.npia.upload.UploadContext
import com.shirochi.npia.upload.UploadItem
import com.shirochi.npia.upload.UploadListener
import com.shirochi.npia.upload.UploadOutcome
import com.shirochi.npia.upload.UploadProgress
import com.shirochi.npia.upload.ZipBundling
import com.shirochi.npia.upload.UploadResult
import com.shirochi.npia.upload.UploaderCatalog
import com.shirochi.npia.webengine.AndroidWebEngine
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

/**
 * App-wide state: settings, the persistent download queue, and upload history (iOS `AppModel`).
 * Lives in the Application so downloads outlive activities; `DownloadService` keeps the process
 * in the foreground while any job is active.
 */
class DownloadManager(private val app: Application) {
    val secrets: SecretStore by lazy {
        // Keystore failures (rare, on some devices after restore) must not crash the app.
        runCatching { EncryptedSecretStore(app) }.getOrElse { InMemorySecretStore() }
    }
    val preferences = AppPreferences(app)
    private val web by lazy { AndroidWebEngine(app, pageLoadSeconds = { _settings.value.shared.pageLoadTimeoutSeconds.toLong() }) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val settingsFile = File(app.filesDir, "settings.json")
    private val jobStore = JsonFileStore(File(app.filesDir, "jobs.json"), ListSerializer(JobItem.serializer()))
    private val historyStore = JsonFileStore(File(app.filesDir, "upload-history.json"), ListSerializer(UploadResult.serializer()))
    /** The last imported desktop config without credentials, so exports keep unknown keys. */
    private val desktopBaseStore = JsonFileStore(File(app.filesDir, "desktop-config-base.json"), JsonObject.serializer())

    private val _settings = MutableStateFlow(loadSettings())
    val settings: StateFlow<AppSettings> = _settings.asStateFlow()

    private val _jobs = MutableStateFlow(JobItem.interrupted(jobStore.load().orEmpty()))
    val jobs: StateFlow<List<JobItem>> = _jobs.asStateFlow()

    private val _uploadHistory = MutableStateFlow(historyStore.load().orEmpty())
    val uploadHistory: StateFlow<List<UploadResult>> = _uploadHistory.asStateFlow()

    private val _uploadBatch = MutableStateFlow<UploadBatch?>(null)
    /** The upload of a whole run, started from the summary notification, while it runs. */
    val uploadBatch: StateFlow<UploadBatch?> = _uploadBatch.asStateFlow()

    private val _uploadStates = MutableStateFlow<Map<String, UploadState>>(emptyMap())
    /** Manual uploads by file path: running with byte progress, then done or failed until dismissed. */
    val uploadStates: StateFlow<Map<String, UploadState>> = _uploadStates.asStateFlow()

    /** One-off messages the UI shows as a snackbar. */
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 16)
    val messages: SharedFlow<String> = _messages.asSharedFlow()

    private val running = ConcurrentHashMap<String, Job>()

    /** Looks for a newer build of the app (once a day, if the user allows) and installs it on request. */
    val appUpdater = AppUpdater(app, scope, preferences, settings = { _settings.value })

    /** The npia tag search, which keeps running (and logging) while its screen is closed. */
    val tagSearch: TagSearchManager = TagSearchManager(
        scope = scope,
        settings = { _settings.value },
        // The search does its own retries and logging, so the HTTP layer must not retry behind its back.
        contextFor = { logger -> sourceContext(logger, maxRetries = 0) },
        onStarted = { DownloadService.start(app) },
    )

    /** The run summary the notification buttons act on; see `RunSummary`. */
    private val lastRunStore = JsonFileStore(File(app.filesDir, "last-run.json"), RunSummary.serializer())

    /** The result of the last upload request (links and failures); the result notification's buttons act on it. */
    private val lastUploadStore = JsonFileStore(File(app.filesDir, "last-upload.json"), UploadReport.serializer())

    /** Every job's log lines in time order: the last 5,000, kept in a rolling file across restarts. */
    val queueLog = QueueLog(File(app.filesDir, "queue-log.jsonl"))

    private val runLock = Any()
    /** Jobs that ended since the queue was last idle, as they ended; cleared when the summary is posted. */
    private val runJobs = LinkedHashMap<String, JobItem>()

    /** App-specific external storage: visible over USB and to file managers under Android/data. */
    val outputDirectory: File get() = app.getExternalFilesDir(null) ?: app.filesDir

    fun message(text: String) {
        _messages.tryEmit(text)
    }

    // Settings

    private fun loadSettings(): AppSettings =
        runCatching { SettingsCoding.decode(settingsFile.readText()) }.getOrDefault(AppSettings())

    fun updateSettings(change: (AppSettings) -> AppSettings) {
        val before = _settings.value.engines
        val beforeAll = _settings.value
        val next = change(_settings.value)
        if (next == _settings.value) return
        _settings.value = next
        runCatching { settingsFile.writeText(SettingsCoding.encode(next)) }
            .onFailure { message("Could not save settings: ${it.message}") }
        // Opting in to updates (a toggle, or an imported config) checks right away.
        val engines = next.engines
        if ((engines.novelDownloaderAutoUpdate && !before.novelDownloaderAutoUpdate) ||
            (engines.webtoepubAutoUpdate && !before.webtoepubAutoUpdate)
        ) {
            checkEngineUpdates(automatic = true)
        }
        // Switching the app update check on looks right away, still at most once a day.
        if (next.appUpdateCheck && !beforeAll.appUpdateCheck) appUpdater.checkIfDue()
    }

    private val _preferencesFlow = MutableStateFlow(0)
    /** Bumped whenever an `AppPreferences` value changes, so screens can observe them. */
    val preferencesFlow: StateFlow<Int> = _preferencesFlow.asStateFlow()

    fun setKeepScreenAwake(value: Boolean) {
        preferences.keepScreenAwake = value
        _preferencesFlow.update { it + 1 }
    }

    fun setCatalogUrl(value: String) {
        preferences.catalogUrl = value
        _preferencesFlow.update { it + 1 }
    }

    fun setTranslateLanguage(value: String) {
        preferences.translateLanguage = value
        _preferencesFlow.update { it + 1 }
    }

    fun setCatalogCartLinks(value: Boolean) {
        preferences.catalogCartLinks = value
        _preferencesFlow.update { it + 1 }
    }

    fun setTheme(value: String) {
        preferences.theme = value
        _preferencesFlow.update { it + 1 }
    }

    fun setAccent(value: String) {
        preferences.accent = value
        _preferencesFlow.update { it + 1 }
    }

    fun setMaxConcurrentJobs(value: Int) {
        preferences.maxConcurrentJobs = value
        _preferencesFlow.update { it + 1 }
        startNext()
    }

    // Rule engine updates

    /** What the Site engines screen shows about update checks. `changes` counts finished checks, so it can refresh the bundle in use. */
    data class EngineUpdateStatus(val checking: Boolean = false, val results: List<UpdateResult> = emptyList(), val changes: Int = 0)

    private val _engineUpdates = MutableStateFlow(EngineUpdateStatus())
    val engineUpdates: StateFlow<EngineUpdateStatus> = _engineUpdates.asStateFlow()
    private val engineChecking = AtomicBoolean(false)

    /** Reads release files over HTTP; GitHub answers 404 until the `engines` release exists. */
    private val engineFetch = EngineFetch { url, timeoutSeconds ->
        val fetcher = OkHttpFetcher(maxRetries = 1, minTimeoutSeconds = _settings.value.shared.requestTimeoutSeconds.toLong())
        val response = fetcher.send(HttpRequest(url, timeoutSeconds = timeoutSeconds))
        if (response.status !in 200..299) throw IOException("HTTP ${response.status}")
        response.body
    }

    /**
     * Looks for rule engine updates in the background. `automatic` is the daily check: only the
     * engines the user opted in to, each at most once per 24 hours, and only news and failures are
     * shown (as messages). Otherwise (the button) every engine is checked and the results are kept
     * for the Site engines screen.
     */
    fun checkEngineUpdates(automatic: Boolean = false) {
        val engines = RuleEngine.ALL.filter { !automatic || _settings.value.engines.autoUpdate(it.engine) }
        if (engines.isEmpty() || !engineChecking.compareAndSet(false, true)) return
        _engineUpdates.update { it.copy(checking = true, results = if (automatic) it.results else emptyList()) }
        scope.launch {
            try {
                for (engine in engines) {
                    val result = try {
                        if (automatic) {
                            RuleEngines.updates.checkIfDue(engine, engineFetch, ::message)
                        } else {
                            RuleEngines.updates.check(engine, engineFetch)
                        }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        UpdateResult(engine, UpdateResult.Status.FAILED, "${engine.title} update check failed: ${e.message ?: e}")
                            .also { if (automatic) message(it.message) }
                    }
                    if (result != null) {
                        _engineUpdates.update { it.copy(results = if (automatic) it.results else it.results + result, changes = it.changes + 1) }
                    }
                }
            } finally {
                engineChecking.set(false)
                _engineUpdates.update { it.copy(checking = false) }
            }
        }
    }

    init {
        // Debug builds keep a test upload server across restarts (see `setUploadTestHost`).
        setUploadTestHost(preferences.uploadTestBase, preferences.uploadTestMaxBytes.takeIf { it > 0 })
        // At start: only for the engines the user opted in to, and at most once a day.
        checkEngineUpdates(automatic = true)
    }

    // Queue

    fun job(id: String): JobItem? = _jobs.value.firstOrNull { it.id == id }

    val activeCount: Int get() = _jobs.value.count { it.state.isActive }

    /** Adds one job per URL; `settingsFor` builds each job's settings for its source. */
    fun enqueue(urls: List<String>, settingsFor: (SourceId?) -> AppSettings) {
        val added = urls.map { url ->
            val source = SourceCatalog.adapterFor(url)?.id
            JobItem(url = SourceCatalog.canonicalUrl(url), source = source, settings = settingsFor(source))
        }
        _jobs.update { added + it }
        added.forEach { jobLog(it.id, LogLevel.INFO, "Queued ${it.source?.raw ?: "unknown source"}: ${it.url}") }
        persistJobs()
        startNext()
    }

    fun cancel(id: String) {
        val task = running[id]
        if (task != null) {
            task.cancel()
        } else if (job(id)?.state == JobState.QUEUED) {
            update(id) { it.copy(state = JobState.CANCELLED, status = "Cancelled") }
            jobLog(id, LogLevel.INFO, "Cancelled while waiting in the queue")
            persistJobs()
            job(id)?.let { endedInRun(it) }
            postRunSummaryIfIdle()
        }
    }

    /** Cancels every queued and running job through the per-job path, so the service and notification follow. */
    fun cancelAll() {
        JobItem.stopOrder(_jobs.value).forEach { cancel(it) }
    }

    fun retry(id: String) {
        val job = job(id) ?: return
        if (job.state.isActive) return
        update(id) { it.resetForRetry() }
        jobLog(id, LogLevel.INFO, "Retry: back in the queue")
        persistJobs()
        startNext()
    }

    fun remove(id: String, deleteFiles: Boolean) {
        running[id]?.cancel()
        val job = job(id) ?: return
        if (deleteFiles) job.files(outputDirectory).forEach { it.deleteRecursively() }
        _jobs.update { list -> list.filterNot { it.id == id } }
        persistJobs()
    }

    fun clearFinished() {
        _jobs.update { list -> list.filter { it.state.isActive } }
        persistJobs()
    }

    @Synchronized
    private fun startNext() {
        while (true) {
            val list = _jobs.value
            val runningCount = list.count { it.state == JobState.RUNNING }
            if (runningCount >= preferences.maxConcurrentJobs) break
            // The queue shows newest first; the oldest queued job runs next.
            val next = list.lastOrNull { it.state == JobState.QUEUED } ?: break
            start(next)
        }
        if (activeCount > 0) DownloadService.start(app)
    }

    private fun start(job: JobItem) {
        val id = job.id
        update(id) { it.copy(state = JobState.RUNNING, status = "Starting") }
        val logger = JobLogging { level, line -> jobLog(id, level, line) }
        jobLog(id, LogLevel.INFO, "Started")
        val pipeline = DownloadPipeline(
            secrets = secrets,
            outputDirectory = outputDirectory,
            web = SnapshotAfterSignIn(web) { snapshotSessions() },
            images = AndroidImageProcessor(job.settings.images),
            logger = logger,
            // Shares cookies with the web engine, so web-view logins apply to HTTP requests.
            httpFactory = { OkHttpFetcher(it, web.cookieJar(), logger) },
        )
        running[id] = scope.launch {
            try {
                val outcome = pipeline.run(job.url, job.settings) { phase -> update(id) { it.applying(phase) } }
                finish(id, outcome)
            } catch (e: CancellationException) {
                update(id) { it.copy(state = JobState.CANCELLED, status = "Cancelled") }
                jobLog(id, LogLevel.INFO, "Cancelled")
            } catch (e: Throwable) {
                val text = e.message ?: e.toString()
                update(id) { it.copy(state = JobState.FAILED, status = text, error = text) }
                jobLog(id, LogLevel.ERROR, "Download failed: ${e.javaClass.simpleName}: $text")
                var cause = e.cause
                while (cause != null && cause !== cause.cause) {
                    jobLog(id, LogLevel.DEBUG, "Caused by ${cause.javaClass.simpleName}: ${cause.message}")
                    cause = cause.cause
                }
                // An expected failure (unsupported address, sign-in needed) says it all; a surprise needs its trace.
                if (e !is com.shirochi.npia.core.SourceException) e.stackTrace.take(8).forEach { jobLog(id, LogLevel.DEBUG, "    at $it") }
            } finally {
                update(id) { it.copy(finishedAt = System.currentTimeMillis()) }
                persistJobs()
                // Recorded before leaving `running`, so a job that ends at the same moment cannot
                // see an idle queue without this one in the summary.
                job(id)?.let { endedInRun(it) }
                running.remove(id)
                startNext()
                postRunSummaryIfIdle()
            }
        }
    }

    private suspend fun finish(id: String, outcome: JobOutcome) {
        val base = outputDirectory.canonicalPath + File.separator
        update(id) { job ->
            job.copy(
                title = outcome.book.title,
                bookId = outcome.book.id,
                fileNames = outcome.files.map { file ->
                    val path = file.canonicalPath
                    if (path.startsWith(base)) path.removePrefix(base) else file.name
                },
                uploads = outcome.uploads,
                skipped = outcome.skipped.map { (ref, reason) -> "${ref.index}. ${ref.title}: $reason" },
                state = JobState.FINISHED,
            ).applying(com.shirochi.npia.pipeline.JobPhase.Finished).let {
                if (outcome.skipped.isEmpty()) it else it.copy(status = "Done, ${outcome.skipped.size} chapters skipped")
            }
        }
        outcome.uploads.forEach { recordUpload(it) }
    }

    private fun endedInRun(job: JobItem) {
        synchronized(runLock) { runJobs[job.id] = job }
    }

    /**
     * Posts the one "Downloaded" notification when the whole queue has gone idle, covering every
     * job that ended since it was last idle (a stopped run reports what finished).
     */
    private fun postRunSummaryIfIdle() {
        if (activeCount > 0 || running.isNotEmpty()) return
        val ended = synchronized(runLock) { runJobs.values.toList().also { runJobs.clear() } }
        val summary = RunSummary.of(ended) ?: return
        lastRunStore.save(summary)
        Notifications.runSummary(app, summary, preferences.shareAgreementAccepted)
    }

    fun lastRun(): RunSummary? = lastRunStore.load()

    private fun update(id: String, change: (JobItem) -> JobItem) {
        _jobs.update { list -> list.map { if (it.id == id) change(it) else it } }
    }

    /**
     * Adds a line to the job's own log (job detail page) and to the combined queue log. The line
     * carries the job's title as it is now, so it reads right after the job is removed.
     */
    private fun jobLog(id: String, level: LogLevel, message: String) {
        val now = System.currentTimeMillis()
        update(id) { it.appendingLog("${LogLines.clock(now)} [${level.name.lowercase()}] $message") }
        queueLog.append(LogEntry(now, id, job(id)?.displayTitle.orEmpty().take(120), level, message))
    }

    /** Jobs of the run in progress: those waiting or running, and those that ended since the queue was last idle. */
    fun runJobIds(): Set<String> =
        _jobs.value.filter { it.state.isActive }.map { it.id }.toSet() + synchronized(runLock) { runJobs.keys.toSet() }

    private fun persistJobs() {
        jobStore.save(_jobs.value)
    }

    // npia account

    private val _npiaSigningIn = MutableStateFlow(false)
    val npiaSigningIn: StateFlow<Boolean> = _npiaSigningIn.asStateFlow()

    private val _npiaAuthVersion = MutableStateFlow(0)
    /** Bumped when a sign-in attempt ends, so screens can re-read the saved session key. */
    val npiaAuthVersion: StateFlow<Int> = _npiaAuthVersion.asStateFlow()

    /** A context for one-off calls outside the download pipeline (sign-in, tag search). */
    fun sourceContext(logger: JobLogging = NullLogger, maxRetries: Int = 1): SourceContext =
        SourceContext(
            OkHttpFetcher(cookieJar = web.cookieJar(), maxRetries = maxRetries, minTimeoutSeconds = _settings.value.shared.requestTimeoutSeconds.toLong()),
            web, secrets, _settings.value, logger,
        )

    /** Signs in with the saved npia account in the background and reports the result as a message. */
    fun signInToNpia() {
        if (!_npiaSigningIn.compareAndSet(false, true)) return
        scope.launch {
            var signedIn = false
            try {
                val email = secrets.get(SecretKey.NPIA_EMAIL)
                val password = secrets.get(SecretKey.NPIA_PASSWORD)
                if (!email.isNullOrEmpty() && !password.isNullOrEmpty()) {
                    signedIn = NpiaAuth.login(email, password, sourceContext(), NpiaCookieSetting { }) != null
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                signedIn = false
            } finally {
                _npiaSigningIn.value = false
                _npiaAuthVersion.update { it + 1 }
            }
            message(if (signedIn) "Signed in to npia" else "npia sign-in failed — check the email and password")
        }
    }

    // Enter App: sfc, qdn and rbooks sessions

    /** Saved copies of the browser-session logins (qdn, rbooks); see `SiteSessions`. */
    val sessions: SiteSessions by lazy {
        web // installs the cookie store the sessions read
        SiteSessions(secrets)
    }

    private val _sessionVersion = MutableStateFlow(0)
    /** Bumped when a sign-in or sign-out ends, so the Enter App screen re-reads the sessions. */
    val sessionVersion: StateFlow<Int> = _sessionVersion.asStateFlow()

    private val _sfcWorking = MutableStateFlow(false)
    val sfcWorking: StateFlow<Boolean> = _sfcWorking.asStateFlow()

    init {
        // A session cookie the web view dropped when the app was killed comes back from the saved copy.
        scope.launch { HardSessions.all.forEach { sessions.restoreOnce(it) } }
    }

    fun siteStatus(site: SessionSite): SessionStatus = sessions.status(site)

    /** Copies the current logins to the saved copies, so a restart cannot lose session cookies. */
    fun snapshotSessions() {
        HardSessions.all.forEach { sessions.snapshot(it) }
        _sessionVersion.update { it + 1 }
    }

    /** Opens the site's sign-in page in the app's own browser; the cookies are the ones downloads use. */
    fun signInToSite(site: SessionSite) {
        scope.launch {
            try {
                web.presentLogin(site.signInUrl, site.source.raw, site.appUserAgentSuffix)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Closed without finishing: keep whatever login is there.
            } finally {
                sessions.snapshot(site)
                // An sfc website sign-in may have been handed an app session; keep it for VIP text.
                if (site.source == SourceId.SFC && !SfcAdapter.hasAppSession(secrets)) {
                    if (SfcAdapter.captureAppSession(SharedCookies.store, secrets)) {
                        message("sfc app session captured — VIP chapters you bought download as text")
                    }
                }
                _sessionVersion.update { it + 1 }
            }
        }
    }

    fun signOutOfSite(site: SessionSite) {
        sessions.signOut(site)
        scope.launch { web.flushCookies() }
        _sessionVersion.update { it + 1 }
        message("Signed out of ${site.source.raw}")
    }

    /** The Browser tab signed out of `host` (or of everything when null): the saved copies must go too. */
    fun forgetSessionsFor(host: String?) {
        (if (host == null) HardSessions.all else HardSessions.forHost(host)).forEach { sessions.forgetSavedCopy(it) }
        _sessionVersion.update { it + 1 }
    }

    fun sfcSignedIn(): Boolean = SfcAdapter.hasAppSession(secrets)

    /** Signs in to the sfc app API; the session cookie is kept in encrypted storage. */
    fun signInToSfc(username: String, password: String) {
        if (!_sfcWorking.compareAndSet(false, true)) return
        scope.launch {
            val ok = try {
                SfcAdapter.login(username, password, sourceContext().http, secrets)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                false
            } finally {
                _sfcWorking.value = false
                _sessionVersion.update { it + 1 }
            }
            message(if (ok) "Signed in to sfc" else "sfc sign-in failed: check the username and password")
        }
    }

    /** Saves a cookie header copied from the sfc app (it must hold `session_APP`). */
    fun saveSfcCookie(cookie: String): Boolean {
        val ok = SfcAdapter.saveAppCookie(cookie, secrets)
        _sessionVersion.update { it + 1 }
        message(if (ok) "sfc app cookie saved" else "That cookie has no session_APP")
        return ok
    }

    fun signOutOfSfc() {
        SfcAdapter.signOut(secrets)
        _sessionVersion.update { it + 1 }
        message("Signed out of sfc")
    }

    // Uploads

    /**
     * The upload settings in effect. A debug build given a test server (see `setUploadTestHost`)
     * sends every upload there instead of to the configured hosts.
     */
    private fun uploadSettings(): UploadSettings {
        val settings = _settings.value.upload
        return if (UploaderCatalog.testHost != null) settings.copy(hosts = listOf(LocalTestUploader.ID)) else settings
    }

    private fun uploadContext(settings: UploadSettings) = UploadContext(secrets, settings, temporaryDirectory = app.cacheDir)

    /**
     * Debug builds only: points uploads at a server on the developer's machine (`base` empty goes
     * back to the real hosts), optionally with a per-file maximum so ZIP splitting can be tried
     * with small files. Ignored in other builds.
     */
    fun setUploadTestHost(base: String, maxBytes: Long?) {
        if (!BuildConfig.DEBUG) return
        UploaderCatalog.testHost = base.trim().takeIf { it.isNotEmpty() }?.let { runCatching { LocalTestUploader(it, maxBytes) }.getOrNull() }
    }

    /** The job whose output includes `file`. */
    private fun jobOwning(file: File): JobItem? {
        val path = file.absolutePath
        return _jobs.value.firstOrNull { job -> job.files(outputDirectory).any { it.absolutePath == path } }
    }

    /** The stem that names `file`'s upload (`npia_123456`); see `UploadNaming`. */
    private fun stemFor(file: File): String? = jobOwning(file)?.let(UploadNaming::forJob)

    /**
     * Uploads `file`; `bookId` is the stem that names it when its title is not usable as an ASCII
     * name, and defaults to the stem of the job it came from.
     */
    fun upload(file: File, jobId: String? = null, bookId: String? = jobId?.let { job(it) }?.let(UploadNaming::forJob)) {
        val key = file.absolutePath
        if (_uploadStates.value[key]?.phase == UploadState.Phase.RUNNING) return
        _uploadStates.update { it + (key to UploadState(key, file.name, jobId, bookId)) }
        val uploadSettings = uploadSettings()
        // Keeps the process alive and shows the progress while the app is in the background.
        DownloadService.start(app)
        if (jobId != null) jobLog(jobId, LogLevel.INFO, "Uploading ${file.name}")
        scope.launch {
            try {
                val result = uploadWithProgress(file, uploadSettings, key, bookId)
                recordUpload(result)
                if (jobId != null) {
                    update(jobId) { it.copy(uploads = it.uploads + result) }
                    persistJobs()
                    jobLog(jobId, LogLevel.INFO, "Uploaded ${result.fileName}: ${result.link}")
                }
                setUpload(key) { it.copy(phase = UploadState.Phase.DONE, link = result.link, host = hostName(result.hostId)) }
                message("Uploaded ${result.fileName}")
            } catch (e: CancellationException) {
                _uploadStates.update { it - key }
                throw e
            } catch (e: Exception) {
                val reason = e.message ?: e.toString()
                setUpload(key) { it.copy(phase = UploadState.Phase.FAILED, error = reason) }
                if (jobId != null) jobLog(jobId, LogLevel.WARNING, "Upload of ${file.name} failed: $reason")
                message("Upload failed: $reason")
            }
        }
    }

    /**
     * Uploads through the configured hosts in order, reporting the host being tried and byte
     * progress (at most every 200 ms) into `uploadStates[key]`.
     */
    private suspend fun uploadWithProgress(file: File, uploadSettings: UploadSettings, key: String, bookId: String?): UploadResult {
        var lastReport = 0L
        return UploaderCatalog.upload(
            file, uploadSettings, uploadContext(uploadSettings), history = null, bookId = bookId,
            progress = { p ->
                val now = System.nanoTime() / 1_000_000
                if (p.sentBytes >= p.totalBytes || now - lastReport >= 200) {
                    lastReport = now
                    setUpload(key) { it.copy(sentBytes = p.sentBytes, totalBytes = p.totalBytes) }
                }
            },
            onHost = { name -> setUpload(key) { it.copy(host = name, sentBytes = 0, totalBytes = 0) } },
        )
    }

    /** The upload items for the finished jobs `ids`: every output file still on disk, named by the job's stem. */
    fun uploadItemsForJobs(ids: Collection<String>): List<UploadItem> =
        _jobs.value.filter { it.id in ids && it.state == JobState.FINISHED }.flatMap { job ->
            val stem = UploadNaming.forJob(job)
            job.files(outputDirectory).filter { it.exists() }.map { UploadItem(it, stem) }
        }

    /**
     * Uploads all files of the finished jobs `ids` (every output format of each). With bundling on
     * and several files they go up as ZIPs of up to the configured size; otherwise one by one.
     * Returns false when the jobs have no files left.
     */
    fun uploadJobs(ids: Collection<String>): Boolean = startBatch(uploadItemsForJobs(ids))

    /**
     * Uploads every file of the run behind a summary notification (bundled like any other upload)
     * and posts the result with the links. Returns false when the run's files are gone.
     */
    fun uploadRun(runId: String?): Boolean {
        val summary = lastRunStore.load()?.takeIf { it.id == runId } ?: return false
        val items = summary.fileNames.mapNotNull { name ->
            val file = File(outputDirectory, name)
            if (file.exists()) UploadItem(file, summary.fileIds[name] ?: UploadNaming.bookId("", null)) else null
        }
        if (items.isEmpty()) return false
        return startBatch(items, fromSummary = true)
    }

    /** Runs the upload again for a failed or finished entry of `uploadStates`: its files, planned afresh. */
    fun retryUpload(state: UploadState): Boolean {
        _uploadStates.update { it - state.path }
        val items = state.paths.map(::File).filter { it.exists() }.map { UploadItem(it, stemFor(it) ?: state.bookId) }
        return startBatch(items)
    }

    private fun startBatch(items: List<UploadItem>, fromSummary: Boolean = false): Boolean {
        if (items.isEmpty()) return false
        val settings = uploadSettings()
        val context = uploadContext(settings)
        val plan = UploaderCatalog.plan(items, settings, context)
        val batchId = UUID.randomUUID().toString()
        if (!_uploadBatch.compareAndSet(null, UploadBatch(batchId, plan.size, zips = plan.count { it is PlannedUpload.Archive }))) {
            message("An upload is still running; wait for it to finish.")
            return true
        }
        if (fromSummary) Notifications.cancel(app, Notifications.SUMMARY_ID)
        DownloadService.start(app)
        scope.launch {
            val outcomes = try {
                UploaderCatalog.uploadAll(plan, settings, context, history = null, listener = BatchListener(batchId))
            } finally {
                _uploadBatch.value = null
            }
            val report = UploadReport(
                uploads = outcomes.mapNotNull { it.result },
                failures = outcomes.filter { !it.succeeded }.map { "${it.name}: ${it.error}" },
                files = items.size,
            )
            lastUploadStore.save(report)
            if (report.uploads.isNotEmpty() && settings.copyLinkToClipboard) {
                withContext(Dispatchers.Main) { copyToClipboard(app, report.shareText) }
            }
            Notifications.uploadResult(app, report)
            message(report.notificationTitle)
        }
        return true
    }

    /** Feeds one batch's progress into `uploadStates`, `uploadBatch`, the job logs and the history. */
    private inner class BatchListener(private val batchId: String) : UploadListener {
        private var key = ""
        private var lastReport = 0L

        private fun jobsOf(upload: PlannedUpload): List<JobItem> = upload.items.mapNotNull { jobOwning(it.file) }.distinctBy { it.id }

        override fun onStart(position: Int, total: Int, upload: PlannedUpload, name: String) {
            val single = upload as? PlannedUpload.Single
            key = single?.item?.file?.absolutePath ?: "zip|$batchId|$position"
            val state = UploadState(
                path = key,
                fileName = name,
                jobId = jobsOf(upload).singleOrNull()?.id,
                bookId = single?.item?.stem,
                paths = upload.items.map { it.file.absolutePath },
                contents = if (upload is PlannedUpload.Archive) upload.items.map { ZipBundling.entryName(it.file) } else emptyList(),
            )
            // A new upload of these files replaces the finished or failed status of an earlier one.
            _uploadStates.update { states ->
                states.filterValues { old -> old.path == key || old.phase == UploadState.Phase.RUNNING || old.paths.none { it in state.paths } } + (key to state)
            }
            _uploadBatch.update { it?.copy(currentPath = key, preparing = null) }
            lastReport = 0L
            jobsOf(upload).forEach { jobLog(it.id, LogLevel.INFO, "Uploading $name ($position of $total)") }
        }

        override fun onPreparing(index: Int, total: Int) {
            val text = "Preparing ZIP $index of $total"
            setUpload(key) { it.copy(preparing = text) }
            _uploadBatch.update { it?.copy(preparing = text) }
        }

        override fun onHost(name: String) {
            setUpload(key) { it.copy(host = name, sentBytes = 0, totalBytes = 0, preparing = null) }
            _uploadBatch.update { it?.copy(preparing = null) }
        }

        override fun onProgress(progress: UploadProgress) {
            val now = System.nanoTime() / 1_000_000
            if (progress.sentBytes >= progress.totalBytes || now - lastReport >= 200) {
                lastReport = now
                setUpload(key) { it.copy(sentBytes = progress.sentBytes, totalBytes = progress.totalBytes) }
            }
        }

        override fun onFinished(position: Int, outcome: UploadOutcome) {
            val result = outcome.result
            val jobs = jobsOf(outcome.upload)
            if (result != null) {
                addToHistory(result)
                setUpload(key) { it.copy(phase = UploadState.Phase.DONE, link = result.link, host = hostName(result.hostId), preparing = null) }
                for (job in jobs) {
                    update(job.id) { it.copy(uploads = it.uploads + result) }
                    jobLog(job.id, LogLevel.INFO, "Uploaded ${result.label}: ${result.link}")
                }
                persistJobs()
            } else {
                val reason = outcome.error ?: "unknown error"
                setUpload(key) { it.copy(phase = UploadState.Phase.FAILED, error = reason, preparing = null) }
                for (job in jobs) jobLog(job.id, LogLevel.WARNING, "Upload of ${outcome.name} failed: $reason")
            }
            _uploadBatch.update { it?.copy(done = it.done + 1, preparing = null) }
        }
    }

    /** The result of the last upload request, which the result notification's buttons act on. */
    fun lastUpload(): UploadReport? = lastUploadStore.load()

    /** Copies the share links (with what each holds) of the upload report to the clipboard; false when it has none. */
    fun copyUploadLinks(reportId: String?): Boolean {
        val report = lastUploadStore.load()?.takeIf { it.id == reportId } ?: return false
        if (report.uploads.isEmpty()) return false
        copyToClipboard(app, report.shareText)
        return true
    }

    fun runningUploads(): List<UploadState> = _uploadStates.value.values.filter { it.phase == UploadState.Phase.RUNNING }

    private fun setUpload(key: String, change: (UploadState) -> UploadState) {
        _uploadStates.update { map -> map[key]?.let { map + (key to change(it)) } ?: map }
    }

    private fun hostName(hostId: String): String = UploaderCatalog.uploader(hostId)?.displayName ?: hostId

    /** Removes a finished or failed upload from `uploadStates`. */
    fun dismissUpload(path: String) {
        _uploadStates.update { map -> if (map[path]?.phase == UploadState.Phase.RUNNING) map else map - path }
    }

    private fun addToHistory(result: UploadResult) {
        _uploadHistory.update { listOf(result) + it }
        historyStore.save(_uploadHistory.value)
    }

    private suspend fun recordUpload(result: UploadResult, copyLink: Boolean = true) {
        addToHistory(result)
        if (copyLink && _settings.value.upload.copyLinkToClipboard) {
            withContext(Dispatchers.Main) { copyToClipboard(app, result.link) }
        }
    }

    fun removeUpload(result: UploadResult) {
        _uploadHistory.update { list -> list.filterNot { it == result } }
        historyStore.save(_uploadHistory.value)
    }

    fun clearUploadHistory() {
        _uploadHistory.value = emptyList()
        historyStore.save(emptyList())
    }

    // Desktop config

    /** Imports an app settings file or a desktop `config.json`; returns a message for the user. */
    fun importConfig(text: String): String {
        return when (val imported = ConfigTransfer.import(text, _settings.value, secrets)) {
            is ConfigTransfer.Imported.AppFile -> {
                updateSettings { imported.settings }
                "Imported app settings."
            }
            is ConfigTransfer.Imported.Desktop -> {
                updateSettings { imported.settings }
                desktopBaseStore.save(imported.base)
                if (imported.storedSecrets > 0) {
                    "Imported settings; ${imported.storedSecrets} credential values moved to encrypted storage."
                } else {
                    "Imported settings."
                }
            }
        }
    }

    fun exportDesktopConfig(): String = ConfigTransfer.exportDesktop(_settings.value, desktopBaseStore.load())

    fun exportAppSettings(): String = ConfigTransfer.exportApp(_settings.value)

    companion object {
        fun copyToClipboard(context: Context, text: String) {
            val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("link", text))
        }
    }
}

/** The web engine the pipeline gets: after a sign-in browser closes, the new login is copied to its saved copy. */
private class SnapshotAfterSignIn(private val inner: WebEngineProviding, private val after: () -> Unit) : WebEngineProviding by inner {
    override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) {
        try {
            inner.presentLogin(url, profile, userAgentSuffix)
        } finally {
            after()
        }
    }
}
