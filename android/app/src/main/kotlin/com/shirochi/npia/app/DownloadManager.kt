package com.shirochi.npia.app

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import com.shirochi.npia.app.model.ConfigTransfer
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.app.model.JsonFileStore
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SettingsCoding
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.images.AndroidImageProcessor
import com.shirochi.npia.pipeline.DownloadPipeline
import com.shirochi.npia.pipeline.JobOutcome
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.sources.SourceCatalog
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

    private val _uploadsInFlight = MutableStateFlow<Set<String>>(emptySet())
    val uploadsInFlight: StateFlow<Set<String>> = _uploadsInFlight.asStateFlow()

    /** One-off messages the UI shows as a snackbar. */
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 16)
    val messages: SharedFlow<String> = _messages.asSharedFlow()

    private val running = ConcurrentHashMap<String, Job>()

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
        }
    }

    fun cancelAll() {
        _jobs.value.filter { it.state.isActive }.forEach { cancel(it.id) }
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
                running.remove(id)
                update(id) { it.copy(finishedAt = System.currentTimeMillis()) }
                persistJobs()
                job(id)?.let { Notifications.jobEnded(app, it) }
                startNext()
            }
        }
    }

    private suspend fun finish(id: String, outcome: JobOutcome) {
        val base = outputDirectory.canonicalPath + File.separator
        update(id) { job ->
            job.copy(
                title = outcome.book.title,
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

    private fun update(id: String, change: (JobItem) -> JobItem) {
        _jobs.update { list -> list.map { if (it.id == id) change(it) else it } }
    }

    private fun persistJobs() {
        jobStore.save(_jobs.value)
    }

    // Uploads

    fun upload(file: File, jobId: String? = null) {
        val key = file.absolutePath
        if (key in _uploadsInFlight.value) return
        _uploadsInFlight.update { it + key }
        val uploadSettings = _settings.value.upload
        scope.launch {
            try {
                val result = UploaderCatalog.upload(file, uploadSettings, UploadContext(secrets, uploadSettings), history = null)
                recordUpload(result)
                if (jobId != null) {
                    update(jobId) { it.copy(uploads = it.uploads + result) }
                    persistJobs()
                }
                message("Uploaded ${result.fileName}")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                message("Upload failed: ${e.message}")
            } finally {
                _uploadsInFlight.update { it - key }
            }
        }
    }

    private suspend fun recordUpload(result: UploadResult) {
        _uploadHistory.update { listOf(result) + it }
        historyStore.save(_uploadHistory.value)
        if (_settings.value.upload.copyLinkToClipboard) {
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
