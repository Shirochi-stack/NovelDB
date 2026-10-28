package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebLoadTimeoutException
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * The web view page of one book, loaded once for every script engine that needs a real page (the
 * novel-downloader rules' web view path and the WebToEpub parsers), like the desktop's single
 * `_open_book_page`.
 *
 * Engines ask for `page()`. The engine that reads the book keeps the page for its chapters
 * (`adopt`); `release` closes it otherwise. A failed load is remembered, so the next engine does
 * not wait for the same failure again. A load that times out is tried once more first: on a first
 * visit nothing is cached, and the second try usually finishes quickly.
 */
internal class SharedBookPage(private val url: String, private val context: SourceContext) {
    private val mutex = Mutex()
    private var page: WebPageDriving? = null
    private var failure: Exception? = null
    private var adopted = false

    /** The page showing the book, opened on first use. */
    suspend fun page(): WebPageDriving = mutex.withLock {
        page?.let { return it }
        failure?.let { throw it }
        try {
            val opened = context.web.newPage(ReaderWebFallback.PROFILE)
            try {
                // Lets WebToEpub fetch sites the page cannot (CORS) through the app's HTTP client.
                opened.exposeTransport { request -> WebToEpubTransport.handle(request, context.http) }
                try {
                    opened.load(url, WebLoadState.DOM_CONTENT_LOADED, LOAD_SECONDS)
                } catch (e: WebLoadTimeoutException) {
                    context.logger.log(LogLevel.INFO, "The page took over $LOAD_SECONDS s to load; trying once more.")
                    opened.load(url, WebLoadState.DOM_CONTENT_LOADED, LOAD_SECONDS)
                }
            } catch (e: Throwable) {
                withContext(NonCancellable) { runCatching { opened.close() } }
                throw e
            }
            page = opened
            opened
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failure = e
            throw e
        }
    }

    /** The engine that read the book keeps the page, so `release` leaves it open. */
    fun adopt() {
        adopted = true
    }

    private companion object {
        const val LOAD_SECONDS = 30L
    }

    /** Closes the page unless an engine adopted it. */
    suspend fun release() {
        val open = mutex.withLock { page.takeUnless { adopted }.also { if (it != null) page = null } }
        if (open != null) withContext(NonCancellable) { runCatching { open.close() } }
    }
}
