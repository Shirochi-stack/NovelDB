package com.shirochi.npia.webengine

import com.shirochi.npia.core.WebEngineException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

/**
 * Shows login sessions to the user. The app implements this (or uses `WebLoginCoordinator`) and
 * shows a screen holding `WebLoginWebView.create(context, session)` with Cancel and Done buttons
 * that call `session.cancel()` / `session.finish()`.
 */
interface WebLoginPresenting {
    /** False when no UI is attached; `presentLogin` then fails. */
    val canPresent: Boolean

    fun present(session: WebLoginSession)

    fun dismiss(session: WebLoginSession)
}

/** One visible sign-in page. Every profile shares the process cookie store (see `AndroidWebEngine`). */
class WebLoginSession(val url: String, val profile: String, val userAgentSuffix: String? = null) {
    val id: String = UUID.randomUUID().toString()

    private val result = CompletableDeferred<Unit>()
    private val _currentUrl = MutableStateFlow<String?>(null)
    private val _title = MutableStateFlow("")
    private val _isLoading = MutableStateFlow(false)

    val currentUrl: StateFlow<String?> = _currentUrl.asStateFlow()
    val title: StateFlow<String> = _title.asStateFlow()
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    /** True once the user finished or cancelled. */
    val isDone: Boolean get() = result.isCompleted

    /** The user finished signing in. */
    fun finish() {
        result.complete(Unit)
    }

    /** The user left the screen without finishing. */
    fun cancel() {
        result.completeExceptionally(WebEngineException("Sign-in was cancelled."))
    }

    /** Called by the login web view as pages load. */
    fun update(url: String?, title: String?, loading: Boolean) {
        _currentUrl.value = url
        if (title != null) _title.value = title
        _isLoading.value = loading
    }

    /** Presents the session and returns when the user taps Done; throws when they cancel. */
    suspend fun run(presenter: WebLoginPresenting) {
        if (!presenter.canPresent) throw WebEngineException("Sign-in screens are not available here.")
        presenter.present(this)
        try {
            result.await()
        } finally {
            if (!result.isCompleted) cancel()
            presenter.dismiss(this)
        }
    }
}

/**
 * Default presenter: publishes the pending session for the UI. The app collects `session` and
 * shows a login screen while it is non-null, and sets `isAttached` while that collector runs.
 */
object WebLoginCoordinator : WebLoginPresenting {
    private val _session = MutableStateFlow<WebLoginSession?>(null)
    val session: StateFlow<WebLoginSession?> = _session.asStateFlow()

    @Volatile
    var isAttached = false

    override val canPresent: Boolean get() = isAttached

    override fun present(session: WebLoginSession) {
        _session.value?.cancel()
        _session.value = session
    }

    override fun dismiss(session: WebLoginSession) {
        _session.compareAndSet(session, null)
    }
}
