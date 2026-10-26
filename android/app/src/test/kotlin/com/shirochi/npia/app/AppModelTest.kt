package com.shirochi.npia.app

import com.shirochi.npia.app.model.ConfigTransfer
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.app.model.SourceInfo
import com.shirochi.npia.app.model.count
import com.shirochi.npia.app.model.difference
import com.shirochi.npia.app.ui.toggleFormat
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SettingsCoding
import com.shirochi.npia.core.SharedOverrides
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.pipeline.JobPhase
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Plain JVM tests for the app's non-UI logic. */
class AppModelTest {
    @Test
    fun parsesUrlsPerLine() {
        assertEquals(
            listOf("https://example.com/a", "https://example.org/b"),
            SourceInfo.urls("https://example.com/a\n\n  example.org/b  \nnot a url\nfoo"),
        )
        assertNull(SourceInfo.parseUrl("two words.com"))
    }

    @Test
    fun extractsUrlsFromSharedText() {
        val shared = "Great novel (read it): https://example.com/book/1?x=2. Also https://example.com/book/1?x=2"
        assertEquals(listOf("https://example.com/book/1?x=2"), SourceInfo.extractUrls(shared))
    }

    @Test
    fun overridesDifferenceKeepsOnlyChangedFields() {
        val base = SharedSettings()
        val diff = SharedOverrides.difference(base.copy(threads = 8, formats = listOf(OutputFormat.PDF)), base)
        assertEquals(SharedOverrides(threads = 8, formats = listOf(OutputFormat.PDF)), diff)
        assertEquals(2, diff.count)
        assertTrue(SharedOverrides.difference(base, base).isEmpty)
    }

    @Test
    fun jobProgressAndPersistence() {
        var job = JobItem(url = "https://example.com/1")
        job = job.applying(JobPhase.FetchingChapters(5, 10))
        assertEquals("Chapters 5/10", job.status)
        assertEquals(0.35, job.fraction, 1e-9)
        job = job.applying(JobPhase.DownloadingImages(0, 0)).copy(state = JobState.RUNNING)
        assertEquals("No images", job.status)

        val json = JobItem.json.encodeToString(ListSerializer(JobItem.serializer()), listOf(job))
        val restored = JobItem.interrupted(JobItem.json.decodeFromString(ListSerializer(JobItem.serializer()), json))
        assertEquals(JobState.FAILED, restored[0].state)
        assertEquals(job.settings, restored[0].settings)
        assertEquals(JobState.QUEUED, restored[0].resetForRetry().state)
    }

    @Test
    fun logIsCapped() {
        var job = JobItem(url = "u")
        repeat(JobItem.MAX_LOG_LINES + 20) { job = job.appendingLog("line $it") }
        assertEquals(JobItem.MAX_LOG_LINES, job.log.size)
        assertEquals("line ${JobItem.MAX_LOG_LINES + 19}", job.log.last())
    }

    @Test
    fun desktopImportMovesCredentialsAndKeepsUnknownKeys() {
        val secrets = InMemorySecretStore()
        val desktop = """{"ext_threads": 3, "image_format": "JPEG", "email": "reader@example.com", "wd": "secret", "custom_unknown": 5}"""
        val imported = ConfigTransfer.import(desktop, AppSettings(), secrets) as ConfigTransfer.Imported.Desktop
        assertEquals(3, imported.settings.shared.threads)
        assertEquals(ImageFormat.JPEG, imported.settings.images.format)
        assertEquals(2, imported.storedSecrets)
        assertEquals("reader@example.com", secrets.get(SecretKey.NPIA_EMAIL))
        assertFalse("email" in imported.base)
        assertFalse("wd" in imported.base)

        val exported = Json.parseToJsonElement(ConfigTransfer.exportDesktop(imported.settings, imported.base)).jsonObject
        assertEquals(5, exported.getValue("custom_unknown").jsonPrimitive.int)
        assertEquals(3, exported.getValue("ext_threads").jsonPrimitive.int)
        assertFalse("email" in exported || "wd" in exported || "loginkey" in exported)
        assertEquals(exported.keys.sorted(), exported.keys.toList())
    }

    @Test
    fun appSettingsFilesAreRecognised() {
        val settings = AppSettings().let { it.copy(shared = it.shared.copy(threads = 9)) }
        val imported = ConfigTransfer.import(SettingsCoding.encode(settings), AppSettings(), InMemorySecretStore())
        assertEquals(ConfigTransfer.Imported.AppFile(settings), imported)
    }

    @Test
    fun formatPickerKeepsOneSelected() {
        assertEquals(listOf(OutputFormat.EPUB), toggleFormat(listOf(OutputFormat.EPUB), OutputFormat.EPUB))
        assertEquals(listOf(OutputFormat.EPUB, OutputFormat.PDF), toggleFormat(listOf(OutputFormat.PDF), OutputFormat.EPUB))
        assertEquals(listOf(OutputFormat.PDF), toggleFormat(listOf(OutputFormat.EPUB, OutputFormat.PDF), OutputFormat.EPUB))
    }
}
