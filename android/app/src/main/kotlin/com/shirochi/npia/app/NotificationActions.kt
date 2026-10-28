package com.shirochi.npia.app

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import java.io.File

/** Request codes that stay distinct per run and action, so a newer run's buttons never reuse an older one's. */
private fun requestCode(runId: String, action: Int): Int = 100 + (runId.hashCode() and 0xFFFF) * 4 + action

/**
 * Opens the Android share sheet for the files or links of a finished run. Notification actions
 * cannot start a chooser from a receiver on recent Android, so the button launches this
 * invisible activity, which shares and finishes at once.
 */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val manager = (application as NpiaApplication).downloads
        val id = intent.getStringExtra(EXTRA_RUN)
        val shared = if (intent.action == ACTION_SHARE_LINKS) {
            // The id is an upload report's: its links, each under what it holds.
            val report = manager.lastUpload()?.takeIf { it.id == id }
            report != null && report.uploads.isNotEmpty() && FileActions.shareText(this, report.shareText)
        } else {
            val summary = manager.lastRun()?.takeIf { it.id == id }
            summary != null && FileActions.share(this, summary.fileNames.map { File(manager.outputDirectory, it) })
        }
        if (!shared) Toast.makeText(this, "Nothing left to share.", Toast.LENGTH_SHORT).show()
        finish()
    }

    companion object {
        const val ACTION_SHARE_FILES = "com.shirochi.npiamobile.SHARE_FILES"
        const val ACTION_SHARE_LINKS = "com.shirochi.npiamobile.SHARE_LINKS"
        private const val EXTRA_RUN = "run"

        fun pending(context: Context, runId: String, action: String): PendingIntent {
            val intent = Intent(context, ShareActivity::class.java).setAction(action).putExtra(EXTRA_RUN, runId)
            val code = requestCode(runId, if (action == ACTION_SHARE_LINKS) 1 else 0)
            return PendingIntent.getActivity(context, code, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        }
    }
}

/** Handles the notification buttons that work in the background: upload the run's files, copy its links. */
class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val manager = (context.applicationContext as NpiaApplication).downloads
        val runId = intent.getStringExtra(EXTRA_RUN)
        val text = when (intent.action) {
            ACTION_UPLOAD_RUN -> when {
                !manager.preferences.shareAgreementAccepted -> "Open NovelDB and agree to the sharing terms first."
                manager.uploadRun(runId) -> null
                else -> "Those downloads are no longer available to upload."
            }
            ACTION_COPY_LINKS -> if (manager.copyUploadLinks(runId)) "Links copied." else "No links to copy."
            else -> null
        }
        if (text != null) Toast.makeText(context, text, Toast.LENGTH_SHORT).show()
    }

    companion object {
        const val ACTION_UPLOAD_RUN = "com.shirochi.npiamobile.UPLOAD_RUN"
        const val ACTION_COPY_LINKS = "com.shirochi.npiamobile.COPY_LINKS"
        private const val EXTRA_RUN = "run"

        /**
         * The summary notification's Upload button. Until the one-time sharing agreement is
         * accepted it opens the app (a receiver may not start an activity from a notification) so
         * the dialog can be shown; afterwards it uploads in the background.
         */
        fun uploadAction(context: Context, runId: String, agreed: Boolean): PendingIntent {
            if (agreed) return pending(context, runId, ACTION_UPLOAD_RUN)
            val intent = Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(MainActivity.EXTRA_UPLOAD_RUN, runId)
            return PendingIntent.getActivity(context, requestCode(runId, 4), intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        }

        fun pending(context: Context, runId: String, action: String): PendingIntent {
            val intent = Intent(context, NotificationActionReceiver::class.java).setAction(action).putExtra(EXTRA_RUN, runId)
            val code = requestCode(runId, if (action == ACTION_COPY_LINKS) 3 else 2)
            return PendingIntent.getBroadcast(context, code, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        }
    }
}
