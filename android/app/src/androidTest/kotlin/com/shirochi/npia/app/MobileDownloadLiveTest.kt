package com.shirochi.npia.app

import android.content.Intent
import android.graphics.Bitmap
import android.view.accessibility.AccessibilityNodeInfo
import androidx.lifecycle.ViewModelProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.SharedSettings
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Opt-in verification through the normal queue, cancellation button, file view, and settings screens. */
@RunWith(AndroidJUnit4::class)
class MobileDownloadLiveTest {
    @Test
    fun appPageChaptersAreReadableThroughTheQueue() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue(InstrumentationRegistry.getArguments().getString("live") == "true")
        val app = instrumentation.targetContext.applicationContext as NpiaApplication
        val activity = instrumentation.startActivitySync(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        lateinit var model: AppViewModel
        instrumentation.runOnMainSync { model = ViewModelProvider(activity)[AppViewModel::class.java] }
        val manager = model.manager
        val settings = AppSettings(shared = SharedSettings(rangeFrom = 1, rangeTo = 2, appendRange = true,
            threads = 2, intervalMin = 1.0, intervalMax = 2.0, formats = listOf(OutputFormat.TXT)))
        val url = "https://011.\u0062\u006f\u006f\u006b\u006b\u006f\u0072.com/" +
            "%EC%95%84%EB%B9%A0%EB%A5%BC-%EA%B5%90%EC%B2%B4%ED%95%98%EB%8A%94-%EB%B2%95"
        try {
            manager.updateSettings { settings }
            manager.enqueue(listOf(url)) { settings }
            val id = manager.jobs.value.first().id
            instrumentation.runOnMainSync { model.openJob(id) }
            withTimeout(180_000) {
                while (manager.jobs.value.first { it.id == id }.state.isActive) delay(200)
            }
            val finished = manager.jobs.value.first { it.id == id }
            assertEquals(finished.log.joinToString("\n"), JobState.FINISHED, finished.state)
            assertTrue(finished.log.joinToString("\n"), finished.log.any { "Chapters: 2 downloaded" in it })
            assertTrue(finished.log.any { "Downloading 2 of 296 chapters" in it })
            val text = finished.files(manager.outputDirectory).single().readText()
            assertTrue("The first chapter has readable text", "넝쿨 속에" in text)
            val syllables = text.filter { it in '\uac00'..'\ud7a3' }
            val finals = syllables.count { (it.code - 0xac00) % 28 != 0 }
            assertTrue("The exported chapters contain readable Korean", syllables.length > 1000 && finals.toDouble() / syllables.length < 0.8)
        } finally {
            manager.cancelAll()
            instrumentation.runOnMainSync { activity.finish() }
        }
    }

    @Test
    fun configuredRangeIsRespectedByTheQueue() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue(InstrumentationRegistry.getArguments().getString("live") == "true")
        val app = instrumentation.targetContext.applicationContext as NpiaApplication
        val activity = instrumentation.startActivitySync(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        lateinit var model: AppViewModel
        instrumentation.runOnMainSync { model = ViewModelProvider(activity)[AppViewModel::class.java] }
        val manager = model.manager
        val settings = AppSettings(shared = SharedSettings(rangeFrom = 4, rangeTo = 6, appendRange = true,
            threads = 3, intervalMin = 1.0, intervalMax = 2.0, formats = listOf(OutputFormat.TXT)))
        try {
            manager.updateSettings { settings }
            manager.enqueue(listOf("https://m.\u006d\u0075\u006e\u0070\u0069\u0061.com/novel/detail/564583")) { settings }
            val id = manager.jobs.value.first().id
            instrumentation.runOnMainSync { model.openJob(id) }
            withTimeout(180_000) {
                while (manager.jobs.value.first { it.id == id }.state.isActive) delay(200)
            }
            val finished = manager.jobs.value.first { it.id == id }
            assertEquals(finished.error.orEmpty(), JobState.FINISHED, finished.state)
            assertTrue(finished.log.any { "Downloading 3 of 151 chapters (from 4 to 6)" in it })
            val chapters = finished.log.mapNotNull { Regex("Chapter (\\d+):").find(it)?.groupValues?.get(1)?.toInt() }
            assertEquals(listOf(4, 5, 6), chapters.sorted())
            assertTrue(finished.log.any { "Chapters: 3 downloaded" in it })
            assertTrue(finished.files(manager.outputDirectory).single().name.endsWith(" (4-6).txt"))
        } finally {
            manager.cancelAll()
            instrumentation.runOnMainSync { activity.finish() }
        }
    }

    @Test
    fun partialGenerationAndFreeOnlyDownloadFinishInTheApp() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue(InstrumentationRegistry.getArguments().getString("live") == "true")
        val app = instrumentation.targetContext.applicationContext as NpiaApplication
        val activity = instrumentation.startActivitySync(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        lateinit var model: AppViewModel
        instrumentation.runOnMainSync { model = ViewModelProvider(activity)[AppViewModel::class.java] }
        val manager = model.manager
        val settings = AppSettings(shared = SharedSettings(threads = 3, intervalMin = 1.0, intervalMax = 2.0,
            skipPaid = true, generateOnStop = true, chapterTiming = true, formats = listOf(OutputFormat.TXT)))
        val url = "https://m.\u006d\u0075\u006e\u0070\u0069\u0061.com/novel/detail/564583"
        try {
            manager.updateSettings { settings }
            manager.enqueue(listOf(url)) { settings }
            val id = manager.jobs.value.first().id
            instrumentation.runOnMainSync { model.openJob(id) }
            withTimeout(180_000) {
                while (manager.jobs.value.first { it.id == id }.log.count { "Chapter " in it } < 2) delay(200)
            }
            withTimeout(10_000) { while (instrumentation.uiAutomation.rootInActiveWindow == null) delay(200) }
            screenshot(app.cacheDir, "before-cancel.png")
            val button = textNodes(instrumentation.uiAutomation.rootInActiveWindow, "Cancel").first()
            assertTrue("The Cancel button responds", (if (button.isClickable) button else button.parent).performAction(AccessibilityNodeInfo.ACTION_CLICK))
            withTimeout(60_000) {
                while (manager.jobs.value.first { it.id == id }.state.isActive) delay(200)
            }
            val stopped = manager.jobs.value.first { it.id == id }
            assertEquals(JobState.CANCELLED, stopped.state)
            assertTrue("Stopped job retains its generated file", stopped.files(manager.outputDirectory).single().length() > 0)
            assertTrue(stopped.log.any { Regex("Chapter \\d+:.*\\(\\d+\\.\\d s\\)").containsMatchIn(it) })
            delay(1_000)
            screenshot(app.cacheDir, "partial-files.png")

            val completeSettings = settings.copy(shared = settings.shared.copy(generateOnStop = false))
            manager.updateSettings { completeSettings }
            manager.enqueue(listOf(url)) { completeSettings }
            val completeId = manager.jobs.value.first().id
            withTimeout(240_000) {
                while (manager.jobs.value.first { it.id == completeId }.state.isActive) delay(500)
            }
            val finished = manager.jobs.value.first { it.id == completeId }
            assertEquals(finished.error.orEmpty(), JobState.FINISHED, finished.state)
            assertTrue(finished.log.any { "Downloading 25 of 151 chapters" in it })
            assertTrue(finished.log.any { "Chapters: 25 downloaded" in it })
            assertTrue(finished.log.any { "Finished in " in it })
            assertTrue(finished.files(manager.outputDirectory).all { it.length() > 0 })

            manager.updateSettings { it.copy(upload = it.upload.copy(hosts = listOf("gdrive"))) }
            instrumentation.runOnMainSync { model.select(Tab.SETTINGS); model.push(Route.Upload) }
            delay(500)
            repeat(5) {
                val root = instrumentation.uiAutomation.rootInActiveWindow
                if (textNodes(root, "Sign in to Google").none { it.isVisibleToUser }) {
                    scrollable(root)?.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
                    delay(200)
                }
            }
            assertTrue("Google sign-in is visible", textNodes(instrumentation.uiAutomation.rootInActiveWindow,
                "Sign in to Google").any { it.isVisibleToUser })
            screenshot(app.cacheDir, "google-upload-settings.png")
        } finally {
            manager.cancelAll()
            instrumentation.runOnMainSync { activity.finish() }
        }
    }

    private fun scrollable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isScrollable) return node
        for (index in 0 until node.childCount) node.getChild(index)?.let { scrollable(it)?.let { found -> return found } }
        return null
    }

    private fun textNodes(node: AccessibilityNodeInfo, text: String): List<AccessibilityNodeInfo> {
        val found = mutableListOf<AccessibilityNodeInfo>()
        if (node.text?.toString() == text || node.contentDescription?.toString() == text) found += node
        for (index in 0 until node.childCount) node.getChild(index)?.let { found += textNodes(it, text) }
        return found
    }

    private fun screenshot(directory: File, name: String) {
        val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        File(directory, name).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }
}
