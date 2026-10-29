package com.shirochi.npia.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.app.model.NotificationTarget
import com.shirochi.npia.app.model.RunSummary
import com.shirochi.npia.app.model.UploadBatch
import com.shirochi.npia.app.model.UploadReport
import com.shirochi.npia.app.model.UploadState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.sample
import kotlinx.coroutines.launch

/** The NovelDB catalog website, hosted on GitHub Pages. */
const val DEFAULT_CATALOG_URL = "https://shirochi-stack.github.io/NovelDB/"

/** NovelDB Next, offered as a suggestion; its cards get link-cart buttons too. */
const val NOVELDB_NEXT_URL = "https://noveldb.pages.dev/"

/**
 * A page of NovelDB (the GitHub Pages site) or NovelDB Next (and its preview deployments): outside
 * links open in the Browser tab and are never renamed or sent to the cart.
 */
fun isNovelDbSite(url: String): Boolean {
    val uri = runCatching { java.net.URI(url.trim()) }.getOrNull() ?: return false
    val host = uri.host?.lowercase() ?: return false
    val next = java.net.URI(NOVELDB_NEXT_URL).host
    if (host == next || host.endsWith(".$next")) return true
    val pages = java.net.URI(DEFAULT_CATALOG_URL)
    val root = pages.path.trimEnd('/')
    val path = uri.path.orEmpty()
    return host == pages.host && (path == root || path.startsWith("$root/"))
}

/**
 * `text` when it is an address the Catalog tab can open: http or https with a site name.
 * "https://" alone, a search phrase or another scheme give null.
 */
fun validCatalogUrl(text: String?): String? {
    val value = text?.trim().orEmpty()
    val uri = runCatching { java.net.URI(value) }.getOrNull() ?: return null
    if (uri.scheme?.lowercase() !in setOf("http", "https")) return null
    if (uri.host.isNullOrBlank()) return null
    return value
}

/** App-level preferences that are not part of the portable `AppSettings` JSON. */
class AppPreferences(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("app", Context.MODE_PRIVATE)

    var keepScreenAwake: Boolean
        get() = prefs.getBoolean("keepScreenAwake", true)
        set(value) = prefs.edit().putBoolean("keepScreenAwake", value).apply()

    /** Downloads that run at once; the rest wait in the queue. */
    var maxConcurrentJobs: Int
        get() = prefs.getInt("maxConcurrentJobs", 1).coerceIn(1, 3)
        set(value) = prefs.edit().putInt("maxConcurrentJobs", value.coerceIn(1, 3)).apply()

    /** `ThemeMode.raw`: system, light, dark or amoled. */
    var theme: String
        get() = prefs.getString("theme", null) ?: "system"
        set(value) = prefs.edit().putString("theme", value).apply()

    /** `Accent.raw`; Halgakos Rose by default. */
    var accent: String
        get() = prefs.getString("accent", null) ?: "halgakos_rose"
        set(value) = prefs.edit().putString("accent", value).apply()

    /** Address the Catalog tab opens; the NovelDB website unless the user picked another site. */
    var catalogUrl: String
        get() = validCatalogUrl(prefs.getString("catalogUrl", null)) ?: DEFAULT_CATALOG_URL
        set(value) = prefs.edit().putString("catalogUrl", validCatalogUrl(value) ?: DEFAULT_CATALOG_URL).apply()

    /**
     * On catalog sites other than NovelDB, links to other sites go to the link cart (and read
     * "⬇️ Download"); off, they open in the Browser tab.
     */
    var catalogCartLinks: Boolean
        get() = prefs.getBoolean("catalogCartLinks", true)
        set(value) = prefs.edit().putBoolean("catalogCartLinks", value).apply()

    /** Language the Browser tab translates pages into (a Google Translate code); English by default. */
    var translateLanguage: String
        get() = prefs.getString("translateLanguage", null) ?: BrowserTranslate.DEFAULT_LANGUAGE
        set(value) = prefs.edit().putString("translateLanguage", BrowserTranslate.language(value).code).apply()

    /** The one-time "Before you share" agreement shown before the first upload. */
    var shareAgreementAccepted: Boolean
        get() = prefs.getBoolean("shareAgreementAccepted", false)
        set(value) = prefs.edit().putBoolean("shareAgreementAccepted", value).apply()

    var askedForNotifications: Boolean
        get() = prefs.getBoolean("askedForNotifications", false)
        set(value) = prefs.edit().putBoolean("askedForNotifications", value).apply()

    /** When the app last asked for a newer build (milliseconds since the epoch); the automatic check runs once per 24 hours. */
    var lastAppUpdateCheck: Long
        get() = prefs.getLong("lastAppUpdateCheck", 0L)
        set(value) = prefs.edit().putLong("lastAppUpdateCheck", value).apply()

    /**
     * Debug builds only: a local test server that uploads go to instead of the real hosts, and
     * its per-file maximum in bytes (0 for none). Set with the `noveldb_upload_base` and
     * `noveldb_upload_max_bytes` extras. Always empty in other builds.
     */
    var uploadTestBase: String
        get() = if (BuildConfig.DEBUG) prefs.getString("uploadTestBase", null).orEmpty() else ""
        set(value) {
            if (BuildConfig.DEBUG) prefs.edit().putString("uploadTestBase", value.trim()).apply()
        }

    var uploadTestMaxBytes: Long
        get() = if (BuildConfig.DEBUG) prefs.getLong("uploadTestMaxBytes", 0L) else 0L
        set(value) {
            if (BuildConfig.DEBUG) prefs.edit().putLong("uploadTestMaxBytes", value.coerceAtLeast(0)).apply()
        }

    /**
     * Debug builds only: replaces the releases address (`https://github.com/.../releases`) so a
     * local server can serve `latest/download/update-android.json` and `download/<tag>/<file>`.
     * Always empty in other builds.
     */
    var updateBaseOverride: String
        get() = if (BuildConfig.DEBUG) prefs.getString("updateBaseOverride", null).orEmpty() else ""
        set(value) {
            if (BuildConfig.DEBUG) prefs.edit().putString("updateBaseOverride", value.trim()).apply()
        }
}

/** What keeps the service alive: downloads, uploads, or a tag search. */
private data class ServiceWork(val jobs: List<JobItem>, val uploads: List<UploadState>, val batch: UploadBatch?, val searching: Boolean)

/**
 * Foreground service that keeps the process alive while downloads run, with a progress
 * notification. It holds partial wake and Wi-Fi locks so downloads continue with the screen off,
 * and stops itself when the queue has nothing active.
 */
class DownloadService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var collector: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    @OptIn(FlowPreview::class)
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val manager = (application as NpiaApplication).downloads
        if (intent?.action == ACTION_CANCEL_ALL) {
            manager.cancelAll()
            manager.tagSearch.stop()
        }
        if (collector == null) {
            goForeground(
                Notifications.progress(
                    this, manager.jobs.value, manager.runningUploads(), manager.uploadBatch.value, manager.tagSearch.running.value,
                    NotificationTarget.forRun(manager.runJobIds()),
                ),
            )
            acquireLocks()
            // Notification updates are throttled; the system drops faster ones anyway. Uploads
            // keep the service alive too, so they finish with the screen off.
            collector = scope.launch {
                combine(manager.jobs, manager.uploadStates, manager.uploadBatch, manager.tagSearch.running) { jobs, uploads, batch, searching ->
                    ServiceWork(jobs, uploads.values.filter { it.phase == UploadState.Phase.RUNNING }, batch, searching)
                }.sample(750).collect { activity ->
                    if (activity.jobs.none { it.state.isActive } && activity.uploads.isEmpty() && activity.batch == null && !activity.searching) {
                        stop()
                    } else {
                        Notifications.notify(
                            this@DownloadService, Notifications.PROGRESS_ID,
                            Notifications.progress(
                                this@DownloadService, activity.jobs, activity.uploads, activity.batch, activity.searching,
                                NotificationTarget.forRun(manager.runJobIds()),
                            ),
                        )
                    }
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun goForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(Notifications.PROGRESS_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(Notifications.PROGRESS_ID, notification)
        }
    }

    @SuppressLint("WakelockTimeout")
    private fun acquireLocks() {
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "NpiaMobile:download").apply {
            setReferenceCounted(false)
            acquire()
        }
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        @Suppress("DEPRECATION")
        wifiLock = wifi?.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "NpiaMobile:download")?.apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun stop() {
        collector?.cancel()
        collector = null
        releaseLocks()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun releaseLocks() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wifiLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        wifiLock = null
    }

    /** Android 15 caps dataSync services at six hours a day; running jobs are cancelled cleanly. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        val manager = (application as NpiaApplication).downloads
        manager.cancelAll()
        manager.message("Android stopped background downloads after its daily limit; retry them later.")
        stop()
    }

    override fun onDestroy() {
        scope.cancel()
        releaseLocks()
        super.onDestroy()
    }

    companion object {
        const val ACTION_CANCEL_ALL = "com.shirochi.npiamobile.CANCEL_ALL"

        fun start(context: Context) {
            val intent = Intent(context, DownloadService::class.java)
            try {
                ContextCompat.startForegroundService(context, intent)
            } catch (e: Exception) {
                // Starting from the background is refused on Android 12+; jobs still run while
                // the app process lives.
            }
        }
    }
}

object Notifications {
    const val PROGRESS_ID = 1
    const val SUMMARY_ID = 2
    const val UPLOAD_RESULT_ID = 3
    private const val CHANNEL_PROGRESS = "downloads"
    private const val CHANNEL_RESULTS = "results"

    @Volatile private var artwork: Bitmap? = null

    /** The Halgakos artwork shown as the large icon of every notification; decoded once. */
    private fun artwork(context: Context): Bitmap? =
        artwork ?: runCatching { BitmapFactory.decodeResource(context.applicationContext.resources, R.drawable.notification_artwork) }
            .getOrNull()?.also { artwork = it }

    fun createChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_PROGRESS, "Download progress", NotificationManager.IMPORTANCE_LOW).apply { setShowBadge(false) },
        )
        manager.createNotificationChannel(NotificationChannel(CHANNEL_RESULTS, "Finished downloads", NotificationManager.IMPORTANCE_DEFAULT))
    }

    /**
     * What a tap opens: the queue's combined log, one book's job page, or the queue. Each target
     * has its own request code, so notifications for different targets never share extras.
     */
    internal fun openApp(context: Context, target: NotificationTarget = NotificationTarget.Queue): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val code = when (target) {
            NotificationTarget.Queue -> {
                intent.putExtra(MainActivity.EXTRA_OPEN_QUEUE, true)
                0
            }
            NotificationTarget.QueueLog -> {
                intent.putExtra(MainActivity.EXTRA_OPEN_LOG, true)
                10
            }
            is NotificationTarget.Job -> {
                intent.putExtra(MainActivity.EXTRA_OPEN_JOB, target.id)
                1000 + (target.id.hashCode() and 0xFFFF)
            }
        }
        return PendingIntent.getActivity(context, code, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    fun progress(
        context: Context,
        jobs: List<JobItem>,
        uploads: List<UploadState> = emptyList(),
        batch: UploadBatch? = null,
        searching: Boolean = false,
        target: NotificationTarget = NotificationTarget.Queue,
    ): Notification {
        val runningJobs = jobs.filter { it.state == JobState.RUNNING }
        val queued = jobs.count { it.state == JobState.QUEUED }
        val first = runningJobs.firstOrNull()
        val upload = if (batch != null) uploads.firstOrNull { it.path == batch.currentPath } else uploads.firstOrNull()
        val cancel = PendingIntent.getService(
            context, 1,
            Intent(context, DownloadService::class.java).setAction(DownloadService.ACTION_CANCEL_ALL),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val extra = buildList {
            if (runningJobs.size > 1) add("${runningJobs.size - 1} more running")
            if (queued > 0) add("$queued queued")
            if (first != null && uploads.isNotEmpty()) add("${uploads.size} uploading")
        }.joinToString(", ")
        // With no download running, an upload (determinate once its size is known) is what shows.
        val fraction = when {
            first != null -> first.fraction
            batch != null -> batch.fraction(upload).toDouble()
            else -> upload?.fraction?.toDouble() ?: 0.0
        }
        val indeterminate = when {
            first != null -> false
            batch != null -> false
            else -> upload == null || upload.totalBytes <= 0
        }
        val uploadLine = if (batch != null) batch.statusLine(upload) else upload?.statusLine
        return NotificationCompat.Builder(context, CHANNEL_PROGRESS)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setLargeIcon(artwork(context))
            .setContentTitle(
                first?.displayTitle ?: (if (batch != null) "Uploading files" else upload?.fileName)
                    ?: (if (searching) "Searching npia" else "Preparing downloads"),
            )
            .setContentText(
                listOfNotNull(first?.status ?: uploadLine ?: (if (searching) "Tag search running" else null), extra.ifEmpty { null })
                    .joinToString(" · "),
            )
            .setProgress(1000, (fraction * 1000).toInt(), indeterminate)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setContentIntent(openApp(context, target))
            .addAction(0, "Cancel all", cancel)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    /**
     * The one notification after a run of the queue: "Downloaded N books" with every file ready to
     * share or upload at once. Failures are counted in its text.
     */
    fun runSummary(context: Context, summary: RunSummary, agreed: Boolean) {
        val builder = NotificationCompat.Builder(context, CHANNEL_RESULTS)
            .setSmallIcon(if (summary.succeeded > 0) android.R.drawable.stat_sys_download_done else android.R.drawable.stat_notify_error)
            .setLargeIcon(artwork(context))
            .setContentTitle(summary.notificationTitle)
            .setContentText(summary.notificationText)
            .setAutoCancel(true)
            // One book opens its job page; several open the queue's combined log.
            .setContentIntent(openApp(context, NotificationTarget.forRun(summary.jobIds)))
        if (summary.hasFiles) {
            builder.addAction(0, "Share", ShareActivity.pending(context, summary.id, ShareActivity.ACTION_SHARE_FILES))
            builder.addAction(0, "Upload", NotificationActionReceiver.uploadAction(context, summary.id, agreed))
        }
        notify(context, SUMMARY_ID, builder.build())
    }

    /**
     * After an upload: "Uploaded 11 files in 3 links" with each link under what it holds
     * ("NovelDB_3_EPUBs.zip (3 books)"), or what went wrong.
     */
    fun uploadResult(context: Context, report: UploadReport) {
        val builder = NotificationCompat.Builder(context, CHANNEL_RESULTS)
            .setSmallIcon(if (report.uploads.isNotEmpty()) android.R.drawable.stat_sys_upload_done else android.R.drawable.stat_notify_error)
            .setLargeIcon(artwork(context))
            .setContentTitle(report.notificationTitle)
            .setContentText(report.notificationText)
            .setStyle(NotificationCompat.BigTextStyle().bigText(report.details))
            .setAutoCancel(true)
            .setContentIntent(openApp(context))
        if (report.uploads.isNotEmpty()) {
            builder.addAction(0, "Share links", ShareActivity.pending(context, report.id, ShareActivity.ACTION_SHARE_LINKS))
            builder.addAction(0, "Copy links", NotificationActionReceiver.pending(context, report.id, NotificationActionReceiver.ACTION_COPY_LINKS))
        }
        notify(context, UPLOAD_RESULT_ID, builder.build())
    }

    fun cancel(context: Context, id: Int) {
        NotificationManagerCompat.from(context).cancel(id)
    }

    fun notify(context: Context, id: Int, notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        NotificationManagerCompat.from(context).notify(id, notification)
    }
}
