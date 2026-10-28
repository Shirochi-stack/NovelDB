package com.shirochi.npia.app

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import androidx.core.content.IntentCompat
import java.io.File
import java.io.IOException

/**
 * Hands a verified APK to Android's `PackageInstaller`. Android asks the user to confirm, refuses an
 * APK signed with another key, and reports the result to `UpdateInstallReceiver`. When the update
 * is installed the system stops the running app, so there is often no "success" to report.
 */
object AppInstaller {
    /** Writes `apk` into an install session for this app and commits it; the result comes back to `UpdateInstallReceiver`. */
    fun install(context: Context, apk: File) {
        val installer = context.packageManager.packageInstaller
        // Sessions an earlier attempt left open would keep their files around.
        installer.mySessions.forEach { runCatching { installer.abandonSession(it.sessionId) } }
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(apk.length())
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_REQUIRED)
        }
        val id = installer.createSession(params)
        try {
            installer.openSession(id).use { session ->
                apk.inputStream().use { input ->
                    session.openWrite("NovelDB.apk", 0, apk.length()).use { out ->
                        input.copyTo(out)
                        session.fsync(out)
                    }
                }
                // Android fills in the result, so the intent must be mutable; it is explicit, so nothing else can receive it.
                val result = PendingIntent.getBroadcast(
                    context, id, Intent(context, UpdateInstallReceiver::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
                )
                session.commit(result.intentSender)
            }
        } catch (e: Exception) {
            runCatching { installer.abandonSession(id) }
            throw if (e is IOException) e else IOException(e.message ?: e.toString(), e)
        }
    }
}

/** What an install result means for the user. */
object InstallOutcome {
    const val SIGNING_KEY =
        "Android refused the update because it is signed with a different key than the NovelDB on this device. " +
            "Uninstall the old NovelDB first; your settings will be lost."

    /** The message for a failed install (`PackageInstaller.STATUS_*`), `message` being Android's own explanation. */
    fun failure(status: Int, message: String?): String {
        val detail = message?.trim().orEmpty()
        return when {
            status == PackageInstaller.STATUS_FAILURE_ABORTED -> "The install was cancelled."
            detail.contains("signature", ignoreCase = true) || detail.contains("UPDATE_INCOMPATIBLE", ignoreCase = true) -> SIGNING_KEY
            detail.contains("DOWNGRADE", ignoreCase = true) -> "Android refused the update because the installed NovelDB is newer."
            status == PackageInstaller.STATUS_FAILURE_STORAGE -> "There is not enough storage space to install the update."
            status == PackageInstaller.STATUS_FAILURE_BLOCKED ->
                "Android blocked the install" + if (detail.isNotEmpty()) ": $detail" else ". Check that installs from NovelDB are allowed."
            else -> "The install failed" + if (detail.isNotEmpty()) ": $detail" else " (status $status)."
        }
    }
}

/** Receives the install session's progress: opens Android's confirmation screen and reports failures to the updater. */
class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val updater = (context.applicationContext as NpiaApplication).downloads.appUpdater
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm = IntentCompat.getParcelableExtra(intent, Intent.EXTRA_INTENT, Intent::class.java)
                if (confirm == null) {
                    updater.installFailed(InstallOutcome.failure(PackageInstaller.STATUS_FAILURE, "Android sent no confirmation screen"))
                } else {
                    try {
                        context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    } catch (e: Exception) {
                        updater.installFailed(InstallOutcome.failure(PackageInstaller.STATUS_FAILURE, e.message))
                    }
                }
            }
            PackageInstaller.STATUS_SUCCESS -> updater.installSucceeded()
            else -> updater.installFailed(InstallOutcome.failure(status, message))
        }
    }
}
