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
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.sample
import kotlinx.coroutines.launch

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

    var askedForNotifications: Boolean
        get() = prefs.getBoolean("askedForNotifications", false)
        set(value) = prefs.edit().putBoolean("askedForNotifications", value).apply()
}

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
        if (intent?.action == ACTION_CANCEL_ALL) manager.cancelAll()
        if (collector == null) {
            goForeground(Notifications.progress(this, manager.jobs.value))
            acquireLocks()
            // Notification updates are throttled; the system drops faster ones anyway.
            collector = scope.launch {
                manager.jobs.sample(750).collect { jobs ->
                    if (jobs.none { it.state.isActive }) {
                        stop()
                    } else {
                        Notifications.notify(this@DownloadService, Notifications.PROGRESS_ID, Notifications.progress(this@DownloadService, jobs))
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
    private const val CHANNEL_PROGRESS = "downloads"
    private const val CHANNEL_RESULTS = "results"

    fun createChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_PROGRESS, "Download progress", NotificationManager.IMPORTANCE_LOW).apply { setShowBadge(false) },
        )
        manager.createNotificationChannel(NotificationChannel(CHANNEL_RESULTS, "Finished downloads", NotificationManager.IMPORTANCE_DEFAULT))
    }

    private fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0,
        Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra(MainActivity.EXTRA_OPEN_QUEUE, true),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    fun progress(context: Context, jobs: List<JobItem>): Notification {
        val runningJobs = jobs.filter { it.state == JobState.RUNNING }
        val queued = jobs.count { it.state == JobState.QUEUED }
        val first = runningJobs.firstOrNull()
        val cancel = PendingIntent.getService(
            context, 1,
            Intent(context, DownloadService::class.java).setAction(DownloadService.ACTION_CANCEL_ALL),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val extra = buildList {
            if (runningJobs.size > 1) add("${runningJobs.size - 1} more running")
            if (queued > 0) add("$queued queued")
        }.joinToString(", ")
        return NotificationCompat.Builder(context, CHANNEL_PROGRESS)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(first?.displayTitle ?: "Preparing downloads")
            .setContentText(listOfNotNull(first?.status, extra.ifEmpty { null }).joinToString(" \u00b7 "))
            .setProgress(1000, ((first?.fraction ?: 0.0) * 1000).toInt(), first == null)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setContentIntent(openApp(context))
            .addAction(0, "Cancel all", cancel)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    fun jobEnded(context: Context, job: JobItem) {
        if (job.state == JobState.CANCELLED) return
        val ok = job.state == JobState.FINISHED
        val notification = NotificationCompat.Builder(context, CHANNEL_RESULTS)
            .setSmallIcon(if (ok) android.R.drawable.stat_sys_download_done else android.R.drawable.stat_notify_error)
            .setContentTitle(if (ok) "Downloaded ${job.displayTitle}" else "Download failed")
            .setContentText(if (ok) job.status else "${job.displayTitle}: ${job.status}")
            .setAutoCancel(true)
            .setContentIntent(openApp(context))
            .build()
        notify(context, 1000 + (job.id.hashCode() and 0xFFFF), notification)
    }

    fun notify(context: Context, id: Int, notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        NotificationManagerCompat.from(context).notify(id, notification)
    }
}
