package com.shirochi.npia.core

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Reuses up to `size` pages of one profile across concurrent chapter tasks, so a batch with
 * `threads = N` keeps at most N web views alive (the desktop opened one Playwright page per worker).
 *
 * @param prepare runs once on every new page (for example `startCapture`).
 */
class WebPagePool(
    private val engine: WebEngineProviding,
    private val profile: String,
    size: Int,
    private val prepare: (suspend (WebPageDriving) -> Unit)? = null,
) {
    private val size = size.coerceAtLeast(1)
    private val mutex = Mutex()
    private val idle = ArrayDeque<WebPageDriving>()
    private val waiters = ArrayDeque<CompletableDeferred<WebPageDriving>>()
    private var created = 0
    private var closed = false

    /** Pages created so far (live or idle). */
    suspend fun pageCount(): Int = mutex.withLock { created }

    suspend fun acquire(): WebPageDriving {
        var pending: CompletableDeferred<WebPageDriving>? = null
        mutex.withLock {
            if (closed) throw CancellationException("Page pool closed")
            idle.removeLastOrNull()?.let { return it }
            if (created < size) {
                created++
            } else {
                pending = CompletableDeferred<WebPageDriving>().also { waiters.addLast(it) }
            }
        }
        val waiter = pending
        if (waiter != null) {
            try {
                return waiter.await()
            } catch (e: CancellationException) {
                mutex.withLock { waiters.remove(waiter) }
                // A page handed over just before cancellation goes back to the pool.
                if (waiter.isCompleted && !waiter.isCancelled) {
                    withContext(NonCancellable) { release(waiter.getCompleted()) }
                }
                throw e
            }
        }
        val page = try {
            engine.newPage(profile)
        } catch (e: Throwable) {
            mutex.withLock { created-- }
            throw e
        }
        prepare?.invoke(page)
        return page
    }

    suspend fun release(page: WebPageDriving) {
        val closeNow = mutex.withLock {
            if (closed) {
                true
            } else {
                val next = waiters.removeFirstOrNull()
                if (next != null) next.complete(page) else idle.addLast(page)
                false
            }
        }
        if (closeNow) withContext(NonCancellable) { page.close() }
    }

    /** Closes idle pages and fails waiting tasks; pages still leased close on release. */
    suspend fun closeAll() {
        val pages = mutex.withLock {
            closed = true
            waiters.forEach { it.cancel() }
            waiters.clear()
            idle.toList().also { idle.clear() }
        }
        withContext(NonCancellable) { pages.forEach { it.close() } }
    }

    /** Leases a page for `body` and returns it to the pool afterwards. */
    suspend fun <T> withPage(body: suspend (WebPageDriving) -> T): T {
        val page = acquire()
        try {
            return body(page)
        } finally {
            withContext(NonCancellable) { release(page) }
        }
    }
}
