package com.shirochi.npia.app

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.SettingsCoding
import com.shirochi.npia.images.AndroidImageProcessor
import com.shirochi.npia.pipeline.DownloadPipeline
import com.shirochi.npia.pipeline.JobPhase
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.webengine.AndroidWebEngine
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.File
import java.util.UUID

data class JobItem(
    val id: String = UUID.randomUUID().toString(),
    val url: String,
    val title: String? = null,
    val phase: JobPhase = JobPhase.Queued,
    val files: List<String> = emptyList(),
    val links: List<String> = emptyList(),
)

/** App-wide state. The UI workstream grows this into the full queue and settings screens. */
class AppViewModel(application: Application) : AndroidViewModel(application) {
    private val settingsFile = File(application.filesDir, "settings.json")
    private val secrets by lazy { EncryptedSecretStore(application) }
    private val web = AndroidWebEngine(application)
    private val running = java.util.concurrent.ConcurrentHashMap<String, Job>()

    private val _settings = MutableStateFlow(loadSettings())
    val settings: StateFlow<AppSettings> = _settings.asStateFlow()

    private val _jobs = MutableStateFlow<List<JobItem>>(emptyList())
    val jobs: StateFlow<List<JobItem>> = _jobs.asStateFlow()

    /** App-specific external storage, visible over USB and in the Files app under Android/data. */
    val outputDirectory: File get() = getApplication<Application>().getExternalFilesDir(null) ?: getApplication<Application>().filesDir

    private fun loadSettings(): AppSettings =
        runCatching { SettingsCoding.decode(settingsFile.readText()) }.getOrDefault(AppSettings())

    fun updateSettings(change: (AppSettings) -> AppSettings) {
        val next = change(_settings.value)
        _settings.value = next
        settingsFile.writeText(SettingsCoding.encode(next))
    }

    fun canDownload(text: String) = SourceCatalog.adapterFor(text.trim()) != null

    fun enqueue(text: String) {
        val url = text.trim()
        val job = JobItem(url = url)
        _jobs.update { listOf(job) + it }
        val pipeline = DownloadPipeline(
            secrets = secrets,
            outputDirectory = outputDirectory,
            web = web,
            images = AndroidImageProcessor(_settings.value.images),
        )
        val settings = _settings.value
        running[job.id] = viewModelScope.launch(Dispatchers.Default) {
            try {
                val outcome = pipeline.run(url, settings) { phase -> update(job.id) { it.copy(phase = phase) } }
                update(job.id) { item ->
                    item.copy(
                        title = outcome.book.title,
                        files = outcome.files.map { it.name },
                        links = outcome.uploads.map { it.link },
                    )
                }
            } catch (e: CancellationException) {
                update(job.id) { it.copy(phase = JobPhase.Failed("Cancelled")) }
                throw e
            } catch (e: Exception) {
                update(job.id) { it.copy(phase = JobPhase.Failed(e.message ?: e.toString())) }
            } finally {
                running.remove(job.id)
            }
        }
    }

    fun cancel(id: String) {
        running[id]?.cancel()
    }

    private fun update(id: String, change: (JobItem) -> JobItem) {
        _jobs.update { list -> list.map { if (it.id == id) change(it) else it } }
    }
}
