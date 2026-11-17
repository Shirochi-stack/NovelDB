package com.shirochi.npia.app

import android.annotation.SuppressLint
import android.app.Activity
import android.app.ActivityManager
import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import com.shirochi.npia.app.model.BackgroundTestMeter
import com.shirochi.npia.app.model.BackgroundTestResult
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

/**
 * Android's battery optimization for this app. While it is on, the system (and many phone makers'
 * own battery savers) may stop the app once it is off screen, foreground service or not; downloads
 * that were running then come back as "Interrupted".
 */
object BatteryOptimization {
    /** True when Android lets the app run in the background without battery limits. */
    fun off(context: Context): Boolean {
        val power = context.getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return true
        return power.isIgnoringBatteryOptimizations(context.packageName)
    }

    /** The user set the app's battery use to "Restricted": nothing runs in the background at all. */
    fun restricted(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return false
        val activity = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager ?: return false
        return activity.isBackgroundRestricted
    }

    /** Android's own "Let app always run in background?" question for this app. */
    @SuppressLint("BatteryLife")
    fun request(context: Context): Intent =
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))

    /** The app's page in the system settings, where its battery use is set. */
    fun appSettings(context: Context): Intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))

    /** Opens the question; a phone without it gets the app's settings page. False when neither opens. */
    fun open(context: Context, launch: (Intent) -> Unit): Boolean =
        listOf(request(context), appSettings(context)).any { intent -> runCatching { launch(intent) }.isSuccess }
}

/**
 * A timed run that shows whether Android lets the app work off screen: the download service is kept
 * up as for a download, a clock ticks every second and the internet is checked every 15 seconds.
 * Its heartbeat is saved as it goes, so a test the system killed is reported at the next start.
 */
class BackgroundTest(private val app: Application, private val scope: CoroutineScope) {
    /** A running test: how far it is, and whether the app is off screen right now. */
    data class Progress(val elapsedMs: Long, val totalMs: Long, val inBackground: Boolean)

    private val prefs = app.getSharedPreferences("background-test", Context.MODE_PRIVATE)
    private var job: Job? = null
    @Volatile private var visible = 0

    private val _progress = MutableStateFlow<Progress?>(null)
    val progress: StateFlow<Progress?> = _progress.asStateFlow()

    private val _running = MutableStateFlow(false)
    /** Keeps the download service up, as an active download does. */
    val running: StateFlow<Boolean> = _running.asStateFlow()

    private val _result = MutableStateFlow(loadResult())
    val result: StateFlow<BackgroundTestResult?> = _result.asStateFlow()

    init {
        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityStarted(activity: Activity) { visible += 1 }
            override fun onActivityStopped(activity: Activity) { visible = (visible - 1).coerceAtLeast(0) }
            override fun onActivityCreated(activity: Activity, state: Bundle?) {}
            override fun onActivityResumed(activity: Activity) {}
            override fun onActivityPaused(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }

    /** A test that was still running when the process ended failed; otherwise the last result. */
    private fun loadResult(): BackgroundTestResult? {
        val started = prefs.getLong(KEY_STARTED, 0L)
        if (started > 0L) {
            val ran = (prefs.getLong(KEY_BEAT, started) - started).coerceAtLeast(0)
            val stopped = BackgroundTestResult.stopped(ran, prefs.getLong(KEY_TOTAL, 0L))
            save(stopped)
            return stopped
        }
        val text = prefs.getString(KEY_RESULT, null) ?: return null
        return BackgroundTestResult(prefs.getBoolean(KEY_PASSED, false), text)
    }

    private fun save(result: BackgroundTestResult) {
        prefs.edit().remove(KEY_STARTED).remove(KEY_BEAT).remove(KEY_TOTAL)
            .putString(KEY_RESULT, result.text).putBoolean(KEY_PASSED, result.passed).apply()
    }

    fun start(totalMs: Long) {
        if (job?.isActive == true) return
        _running.value = true
        _progress.value = Progress(0, totalMs, visible == 0)
        DownloadService.start(app)
        job = scope.launch {
            val began = SystemClock.elapsedRealtime()
            val wallStart = System.currentTimeMillis()
            val meter = BackgroundTestMeter(totalMs, began)
            prefs.edit().putLong(KEY_STARTED, wallStart).putLong(KEY_BEAT, wallStart).putLong(KEY_TOTAL, totalMs).apply()
            // Internet checks run beside the clock, so a slow answer is never counted as a pause.
            val checks = launch {
                while (isActive) {
                    delay(CHECK_EVERY_MS)
                    val background = visible == 0
                    val answered = reachable()
                    if (background && visible == 0) synchronized(meter) { meter.check(answered) }
                }
            }
            try {
                while (true) {
                    delay(1000)
                    val now = SystemClock.elapsedRealtime()
                    val background = visible == 0
                    synchronized(meter) { meter.tick(now, background) }
                    prefs.edit().putLong(KEY_BEAT, wallStart + (now - began)).apply()
                    _progress.value = Progress((now - began).coerceAtMost(totalMs), totalMs, background)
                    if (now - began >= totalMs) break
                }
                checks.cancel()
                val result = synchronized(meter) { meter.result() }
                save(result)
                _result.value = result
                Notifications.backgroundTest(app, result)
            } finally {
                checks.cancel()
                // Stopped by the user: no result, and nothing left for the next start to report.
                prefs.edit().remove(KEY_STARTED).remove(KEY_BEAT).remove(KEY_TOTAL).apply()
                _progress.value = null
                _running.value = false
            }
        }
    }

    fun stop() {
        job?.cancel()
    }

    private suspend fun reachable(): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val connection = URL(DEFAULT_CATALOG_URL).openConnection() as HttpURLConnection
            try {
                connection.requestMethod = "HEAD"
                connection.connectTimeout = 8000
                connection.readTimeout = 8000
                connection.useCaches = false
                connection.responseCode in 200..399
            } finally {
                connection.disconnect()
            }
        }.getOrDefault(false)
    }

    companion object {
        private const val KEY_STARTED = "started"
        private const val KEY_BEAT = "beat"
        private const val KEY_TOTAL = "total"
        private const val KEY_RESULT = "result"
        private const val KEY_PASSED = "passed"
        private const val CHECK_EVERY_MS = 15_000L
    }
}
