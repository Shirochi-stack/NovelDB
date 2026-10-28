package com.shirochi.npia.app

import android.app.Application
import android.util.Log
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.sources.engines.RuleEngines
import com.shirochi.npia.webengine.AndroidWebEngine
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * Reads a book that only WebToEpub knows through the real web view and the bundled parsers. It
 * needs the internet and a site that is up, so it only runs when asked:
 *
 *     adb shell am instrument -w -e live true -e class com.shirochi.npia.app.WebToEpubLiveTest \
 *         com.shirochi.npiamobile.test/androidx.test.runner.AndroidJUnitRunner
 */
@RunWith(AndroidJUnit4::class)
class WebToEpubLiveTest {
    private val url = "https://novelfull.com/the-legendary-mechanic.html"

    @Test
    fun readsABookAndAChapterThroughTheWebView() = runBlocking {
        assumeTrue("pass -e live true to run", InstrumentationRegistry.getArguments().getString("live") == "true")
        val app = ApplicationProvider.getApplicationContext<Application>()
        RuleEngines.configure(File(app.cacheDir, "live-engines"))

        val web = AndroidWebEngine(app)
        val http = OkHttpFetcher(cookieJar = web.cookieJar(), maxRetries = 1)
        val log = JobLogging { level, message -> Log.i("W2ELive", "[$level] $message") }
        val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, threads = 1))
        val context = com.shirochi.npia.core.SourceContext(http, web, InMemorySecretStore(), settings, log)

        val adapter = SourceCatalog.require(url, settings.engines)
        val book = adapter.parseBook(url, context)
        Log.i("W2ELive", "book: ${book.title} by ${book.author}, ${book.chapters.size} chapters, cover ${book.coverUrl}")
        assertTrue("the book has a title", book.title.isNotBlank())
        assertTrue("the book has chapters", book.chapters.isNotEmpty())

        val results = adapter.parseChapters(book.chapters.take(1), book, context)
        val chapter = results.single()
        Log.i("W2ELive", "chapter: $chapter".take(600))
        assertTrue("the first chapter has text: $chapter", chapter is ChapterResult.Content)
        val html = (chapter as ChapterResult.Content).content.html
        assertTrue("the chapter has paragraphs", html.contains("<p>"))
    }
}
