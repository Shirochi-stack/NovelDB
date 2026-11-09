package com.shirochi.npia.app

import android.app.Application
import android.content.Intent
import android.util.Log
import android.webkit.CookieManager
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.sources.mpia.MpiaAdapter
import com.shirochi.npia.webengine.AndroidWebEngine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import kotlin.coroutines.resume

/** Opt-in check of free and already-owned paid chapters through the app's real WebView. */
@RunWith(AndroidJUnit4::class)
class MpiaLiveTest {
    @Test
    fun readsSelectedChaptersThroughTheWebView() = runBlocking {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue("pass -e live true to run", args.getString("live") == "true")
        val app = ApplicationProvider.getApplicationContext<Application>()
        val url = args.getString("mpia_url") ?: "https://m.\u006d\u0075\u006e\u0070\u0069\u0061.com/novel/detail/564583"
        val selected = (args.getString("mpia_chapters") ?: "4,26").split(',').map { it.trim().toInt() }
        val web = AndroidWebEngine(app)

        // A local diagnostic can supply its existing session without putting credentials in arguments or logs.
        if (args.getString("mpia_session_cache") == "true") {
            val file = File(app.cacheDir, "mpia-live-session.json")
            try {
                val cookies = Json.parseToJsonElement(file.readText()).jsonArray
                withContext(Dispatchers.Main) {
                    for (entry in cookies) {
                        val cookie = entry.jsonObject
                        val accepted = suspendCancellableCoroutine<Boolean> { continuation ->
                            CookieManager.getInstance().setCookie(
                                cookie.getValue("origin").jsonPrimitive.content,
                                cookie.getValue("cookie").jsonPrimitive.content,
                            ) { continuation.resume(it) }
                        }
                        assertTrue("The saved session cookie was accepted", accepted)
                    }
                }
            } finally {
                file.delete()
            }
        }

        val settings = AppSettings(shared = SharedSettings(threads = 3, intervalMin = 1.0, intervalMax = 2.0))
        val logger = JobLogging { level, message -> Log.i("MpiaLive", "[$level] $message") }
        val http = OkHttpFetcher(cookieJar = web.cookieJar(), maxRetries = 1)
        val context = SourceContext(http, web, InMemorySecretStore(), settings, logger)
        val adapter = MpiaAdapter()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val activity = instrumentation.startActivitySync(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        try {
            val book = adapter.parseBook(url, context)
            Log.i("MpiaLive", "Book ${book.id}, ${book.chapters.size} chapters")
            val refs = book.chapters.filter { it.index in selected }
            assertEquals("All selected chapters exist", selected.size, refs.size)
            val results = adapter.parseChapters(refs, book, context)
            for (result in results) {
                val summary = when (result) {
                    is ChapterResult.Content -> "content, ${result.content.html.length} HTML characters"
                    is ChapterResult.Refused -> "refused, ${result.reason}"
                    is ChapterResult.Failed -> "failed, ${result.message}"
                }
                Log.i("MpiaLive", "Chapter ${result.ref.index}, paid=${result.ref.isPaid}, $summary")
                assertTrue("Chapter ${result.ref.index}: $summary", result is ChapterResult.Content)
            }
        } finally {
            instrumentation.runOnMainSync { activity.finish() }
        }
    }
}
