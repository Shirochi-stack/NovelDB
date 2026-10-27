package com.shirochi.npia.app

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import com.shirochi.npia.app.model.ConfigTransfer
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.app.model.JsonFileStore
import com.shirochi.npia.app.model.RunSummary
import com.shirochi.npia.app.model.UploadBatch
import com.shirochi.npia.app.model.UploadNaming
import com.shirochi.npia.app.model.UploadState
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.NullLogger
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SettingsCoding
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.UploadSettings
import com.shirochi.npia.images.AndroidImageProcessor
import com.shirochi.npia.pipeline.DownloadPipeline
import com.shirochi.npia.pipeline.JobOutcome
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.sources.npia.NpiaAuth
import com.shirochi.npia.sources.npia.NpiaCookieSetting
import com.shirochi.npia.upload.UploadContext
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
import java.util.concurrent.ConcurrentHashMap

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
    private val web by lazy { AndroidWebEngine(app) }
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

    /** The run summary the notification buttons act on; see `RunSummary`. */
    private val lastRunStore = JsonFileStore(File(app.filesDir, "last-run.json"), RunSummary.serializer())

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
        val next = change(_settings.value)
        if (next == _settings.value) return
        _settings.value = next
        runCatching { settingsFile.writeText(SettingsCoding.encode(next)) }
            .onFailure { message("Could not save settings: ${it.message}") }
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

    // Queue

    fun job(id: String): JobItem? = _jobs.value.firstOrNull { it.id == id }

    val activeCount: Int get() = _jobs.value.count { it.state.isActive }

    /** Adds one job per URL; `settingsFor` builds each job's settings for its source. */
    fun enqueue(urls: List<String>, settingsFor: (SourceId?) -> AppSettings) {
        val added = urls.map { url ->
            val source = SourceCatalog.adapterFor(url)?.id
            JobItem(url = url, source = source, settings = settingsFor(source))
        }
        _jobs.update { added + it }
        persistJobs()
        startNext()
    }

    fun cancel(id: String) {
        val task = running[id]
        if (task != null) {
            task.cancel()
        } else if (job(id)?.state == JobState.QUEUED) {
            update(id) { it.copy(state = JobState.CANCELLED, status = "Cancelled") }
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
        val logger = JobLogging { level, line -> update(id) { it.appendingLog("[${level.name.lowercase()}] $line") } }
        val pipeline = DownloadPipeline(
            secrets = secrets,
            outputDirectory = outputDirectory,
            web = web,
            images = AndroidImageProcessor(job.settings.images),
            logger = logger,
            // Shares cookies with the web engine, so web-view logins apply to HTTP requests.
            httpFactory = { OkHttpFetcher(it, web.cookieJar()) },
        )
        running[id] = scope.launch {
            try {
                val outcome = pipeline.run(job.url, job.settings) { phase -> update(id) { it.applying(phase) } }
                finish(id, outcome)
            } catch (e: CancellationException) {
                update(id) { it.copy(state = JobState.CANCELLED, status = "Cancelled") }
            } catch (e: Throwable) {
                val text = e.message ?: e.toString()
                update(id) { it.copy(state = JobState.FAILED, status = text, error = text) }
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
        SourceContext(OkHttpFetcher(cookieJar = web.cookieJar(), maxRetries = maxRetries), web, secrets, _settings.value, logger)

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

    // Uploads

    /**
     * Uploads `file`; `bookId` names it when its file name is not plain ASCII, and defaults to the
     * id of the job it came from.
     */
    fun upload(file: File, jobId: String? = null, bookId: String? = jobId?.let { job(it) }?.let(UploadNaming::forJob)) {
        val key = file.absolutePath
        if (_uploadStates.value[key]?.phase == UploadState.Phase.RUNNING) return
        _uploadStates.update { it + (key to UploadState(key, file.name, jobId, bookId)) }
        val uploadSettings = _settings.value.upload
        // Keeps the process alive and shows the progress while the app is in the background.
        DownloadService.start(app)
        scope.launch {
            try {
                val result = uploadWithProgress(file, uploadSettings, key, bookId)
                recordUpload(result)
                if (jobId != null) {
                    update(jobId) { it.copy(uploads = it.uploads + result) }
                    persistJobs()
                }
                setUpload(key) { it.copy(phase = UploadState.Phase.DONE, link = result.link, host = hostName(result.hostId)) }
                message("Uploaded ${result.fileName}")
            } catch (e: CancellationException) {
                _uploadStates.update { it - key }
                throw e
            } catch (e: Exception) {
                val reason = e.message ?: e.toString()
                setUpload(key) { it.copy(phase = UploadState.Phase.FAILED, error = reason) }
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
            file, uploadSettings, UploadContext(secrets, uploadSettings), history = null, bookId = bookId,
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

    /**
     * Uploads every file of the run behind a summary notification, one after another through the
     * configured hosts, then posts "Uploaded N files" with the links. Returns false when the run's
     * files are gone.
     */
    fun uploadRun(runId: String?): Boolean {
        val summary = lastRunStore.load()?.takeIf { it.id == runId } ?: return false
        val files = summary.fileNames.map { File(outputDirectory, it) }.filter { it.exists() }
        if (files.isEmpty()) return false
        if (!_uploadBatch.compareAndSet(null, UploadBatch(summary.id, files.size))) return true
        Notifications.cancel(app, Notifications.SUMMARY_ID)
        DownloadService.start(app)
        val uploadSettings = _settings.value.upload
        scope.launch {
            val links = mutableListOf<String>()
            val failures = mutableListOf<String>()
            try {
                for (file in files) {
                    val key = file.absolutePath
                    val bookId = summary.fileIds[file.relativeTo(outputDirectory).path] ?: UploadNaming.bookId("", null)
                    _uploadStates.update { it + (key to UploadState(key, file.name, bookId = bookId)) }
                    _uploadBatch.update { it?.copy(currentPath = key) }
                    try {
                        val result = uploadWithProgress(file, uploadSettings, key, bookId)
                        recordUpload(result, copyLink = false)
                        links += result.link
                        setUpload(key) { it.copy(phase = UploadState.Phase.DONE, link = result.link, host = hostName(result.hostId)) }
                    } catch (e: CancellationException) {
                        _uploadStates.update { it - key }
                        throw e
                    } catch (e: Exception) {
                        val reason = e.message ?: e.toString()
                        failures += "${file.name}: $reason"
                        setUpload(key) { it.copy(phase = UploadState.Phase.FAILED, error = reason) }
                    }
                    _uploadBatch.update { it?.copy(done = it.done + 1) }
                }
            } finally {
                _uploadBatch.value = null
            }
            val finished = summary.copy(links = links)
            lastRunStore.save(finished)
            if (links.isNotEmpty() && _settings.value.upload.copyLinkToClipboard) {
                withContext(Dispatchers.Main) { copyToClipboard(app, links.joinToString("\n")) }
            }
            Notifications.uploadResult(app, finished, uploaded = links.size, failures = failures)
        }
        return true
    }

    /** Copies the share links of the run's upload to the clipboard; false when it has none. */
    fun copyRunLinks(runId: String?): Boolean {
        val links = lastRunStore.load()?.takeIf { it.id == runId }?.links.orEmpty()
        if (links.isEmpty()) return false
        copyToClipboard(app, links.joinToString("\n"))
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

    private suspend fun recordUpload(result: UploadResult, copyLink: Boolean = true) {
        _uploadHistory.update { listOf(result) + it }
        historyStore.save(_uploadHistory.value)
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
