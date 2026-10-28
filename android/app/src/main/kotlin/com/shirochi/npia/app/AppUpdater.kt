package com.shirochi.npia.app

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import com.shirochi.npia.core.AppBuild
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.AppUpdateUrls
import com.shirochi.npia.core.UpdateOffer
import com.shirochi.npia.core.UpdateSchedule
import com.shirochi.npia.net.AppUpdateClient
import com.shirochi.npia.net.UpdateCheck
import com.shirochi.npia.net.UpdateVerificationException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.File
import java.io.IOException

/** What the update notice and the App updates screen show. */
sealed interface AppUpdateState {
    /** Nothing known yet. */
    data object Idle : AppUpdateState

    data object Checking : AppUpdateState

    /** The outcome of a check that found nothing to install, or a failed step, as a sentence. */
    data class Message(val text: String) : AppUpdateState

    /** A newer build exists. `notice` is whether the "update available" dialog shows; "Later" turns it off. */
    data class Available(val offer: UpdateOffer, val notice: Boolean) : AppUpdateState

    /** Android has to be told once that NovelDB may install apps; the dialog explains and opens that settings page. */
    data class NeedsPermission(val offer: UpdateOffer) : AppUpdateState

    data class Downloading(val offer: UpdateOffer, val received: Long, val total: Long) : AppUpdateState

    /** The verified file went to Android's installer, which asks the user to confirm. */
    data class Installing(val offer: UpdateOffer) : AppUpdateState

    /** The download or install failed; `text` says why. */
    data class Failed(val offer: UpdateOffer, val text: String) : AppUpdateState
}

/**
 * Looks for a newer build of the app and installs it when the user taps Update (`AppUpdate.kt` has
 * the rules shared with desktop and iOS). Nothing is downloaded before the tap, nothing is installed
 * before the file's size and SHA-256 match the manifest, and a development build never offers updates.
 */
class AppUpdater(
    private val app: Application,
    private val scope: CoroutineScope,
    private val preferences: AppPreferences,
    private val settings: () -> AppSettings,
    private val client: AppUpdateClient = AppUpdateClient(),
    private val clock: () -> Long = System::currentTimeMillis,
) {
    /** The build this app was made from; no build number means a development build. */
    val build = AppBuild(BuildConfig.NOVELDB_BUILD, BuildConfig.NOVELDB_TAG, BuildConfig.NOVELDB_LABEL)

    private val _state = MutableStateFlow<AppUpdateState>(AppUpdateState.Idle)
    val state: StateFlow<AppUpdateState> = _state.asStateFlow()

    private var job: Job? = null

    /** Where the update is saved; cleaned out whenever a download starts and at app start. */
    private val directory: File get() = File(app.cacheDir, "app-update")

    init {
        // A file left by an earlier run (this very update, after installing) is of no use.
        scope.launch { directory.deleteRecursively() }
    }

    private fun urls() = AppUpdateUrls(preferences.updateBaseOverride.ifEmpty { null })

    private fun busy(state: AppUpdateState) =
        state is AppUpdateState.Checking || state is AppUpdateState.Downloading || state is AppUpdateState.Installing

    /** The automatic check: when the setting is on, at most once per 24 hours, and never for a development build. */
    fun checkIfDue() {
        val state = _state.value
        // An update that is already known, or one in progress, needs no second look.
        if (busy(state) || state is AppUpdateState.Available || state is AppUpdateState.NeedsPermission) return
        if (!UpdateSchedule.automaticCheckDue(settings().appUpdateCheck, build, preferences.lastAppUpdateCheck, clock())) return
        check(automatic = true)
    }

    /** "Check for app updates now". */
    fun checkNow() {
        if (busy(_state.value)) return
        check(automatic = false)
    }

    private fun check(automatic: Boolean) {
        if (build.isDevelopment) {
            if (!automatic) _state.value = AppUpdateState.Message(AppBuild.DEVELOPMENT_STATUS)
            return
        }
        _state.value = AppUpdateState.Checking
        job = scope.launch {
            val result = client.check(build, urls())
            // A failed attempt is not counted, so the next start or return to the app tries again.
            if (result !is UpdateCheck.Failed) preferences.lastAppUpdateCheck = clock()
            _state.value = when (result) {
                UpdateCheck.Development -> AppUpdateState.Message(AppBuild.DEVELOPMENT_STATUS)
                UpdateCheck.NoInformation -> AppUpdateState.Message("No update information is published for this app yet.")
                UpdateCheck.UpToDate -> AppUpdateState.Message("NovelDB ${build.version} is up to date.")
                is UpdateCheck.Available -> AppUpdateState.Available(result.offer, notice = true)
                is UpdateCheck.Failed -> AppUpdateState.Message("Could not check for updates: ${result.message}")
            }
        }
    }

    /** True when Android lets NovelDB install apps (the "install unknown apps" switch for this app). */
    fun canInstall(): Boolean = app.packageManager.canRequestPackageInstalls()

    /** Update: asks for the install permission if needed, then downloads, verifies and hands the file to the installer. */
    fun startUpdate() {
        val offer = when (val state = _state.value) {
            is AppUpdateState.Available -> state.offer
            is AppUpdateState.NeedsPermission -> state.offer
            is AppUpdateState.Failed -> state.offer
            else -> return
        }
        if (!canInstall()) {
            _state.value = AppUpdateState.NeedsPermission(offer)
            return
        }
        _state.value = AppUpdateState.Downloading(offer, 0, offer.asset.size)
        job = scope.launch {
            try {
                directory.deleteRecursively()
                var reported = -1L
                val file = client.download(offer, urls().asset(offer.tag, offer.asset.name), directory) { received, total ->
                    // About a hundred progress steps are enough.
                    val step = received * 100 / total
                    if (step != reported) {
                        reported = step
                        // Only while still downloading: a cancel that just happened must stay cancelled.
                        val current = _state.value
                        if (current is AppUpdateState.Downloading) _state.compareAndSet(current, AppUpdateState.Downloading(offer, received, total))
                    }
                }
                // The file sits in app-private storage, but what goes to the installer is checked once more.
                AppUpdateClient.verify(file, offer.asset)
                _state.value = AppUpdateState.Installing(offer)
                AppInstaller.install(app, file)
            } catch (e: CancellationException) {
                directory.deleteRecursively()
                throw e
            } catch (e: UpdateVerificationException) {
                directory.deleteRecursively()
                _state.value = AppUpdateState.Failed(offer, e.message ?: UpdateVerificationException.MISMATCH)
            } catch (e: IOException) {
                directory.deleteRecursively()
                _state.value = AppUpdateState.Failed(offer, "Could not download the update: ${e.message ?: e}")
            } catch (e: Exception) {
                directory.deleteRecursively()
                _state.value = AppUpdateState.Failed(offer, "Could not start the install: ${e.message ?: e}")
            }
        }
    }

    /** Cancel on the progress dialog. */
    fun cancel() {
        val state = _state.value as? AppUpdateState.Downloading ?: return
        job?.cancel()
        directory.deleteRecursively()
        _state.value = AppUpdateState.Available(state.offer, notice = false)
    }

    /** Later: hides the notice. The update stays available on the App updates screen. */
    fun dismissNotice() {
        val offer = when (val state = _state.value) {
            is AppUpdateState.Available -> state.offer
            is AppUpdateState.NeedsPermission -> state.offer
            is AppUpdateState.Failed -> state.offer
            is AppUpdateState.Installing -> state.offer
            else -> return
        }
        _state.value = AppUpdateState.Available(offer, notice = false)
    }

    /** The app came to the foreground: continue an update that waited for the install permission, else run the daily check. */
    fun onForeground() {
        if (_state.value is AppUpdateState.NeedsPermission && canInstall()) startUpdate() else checkIfDue()
    }

    /** Opens Android's "install unknown apps" page for NovelDB. */
    fun openInstallSettings(context: Context) {
        val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${app.packageName}"))
        if (context !is android.app.Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            context.startActivity(intent)
        } catch (e: Exception) {
            context.startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS).also { if (context !is android.app.Activity) it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) })
        }
    }

    /** From `UpdateInstallReceiver`. */
    fun installFailed(text: String) {
        directory.deleteRecursively()
        val offer = (_state.value as? AppUpdateState.Installing)?.offer
            ?: (_state.value as? AppUpdateState.Available)?.offer
            ?: return
        _state.value = AppUpdateState.Failed(offer, text)
    }

    fun installSucceeded() {
        directory.deleteRecursively()
        _state.value = AppUpdateState.Message("NovelDB was updated.")
    }
}
