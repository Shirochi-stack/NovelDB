package com.shirochi.npia.app.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppUpdateState
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.FileActions
import java.util.Locale

internal fun formatMegabytes(bytes: Long): String = String.format(Locale.US, "%.1f MB", bytes / 1_048_576.0)

/**
 * The update notice and the steps after Update: "NovelDB v98 hotfix3 is available" with Update,
 * Release notes and Later; the install permission explanation; the download with Cancel; and a
 * failure. Nothing is downloaded or installed before the user taps Update.
 */
@Composable
fun AppUpdateDialog(model: AppViewModel) {
    val updater = model.manager.appUpdater
    val state by updater.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    when (val s = state) {
        is AppUpdateState.Available -> if (s.notice) {
            AlertDialog(
                onDismissRequest = { updater.dismissNotice() },
                title = { Text(s.offer.title) },
                text = { Text("You have ${updater.build.version}. The download is ${formatMegabytes(s.offer.asset.size)}.") },
                confirmButton = { TextButton(onClick = { updater.startUpdate() }) { Text("Update") } },
                dismissButton = {
                    Row {
                        TextButton(onClick = { updater.dismissNotice() }) { Text("Later") }
                        TextButton(onClick = { FileActions.openUrl(context, s.offer.releaseUrl) }) { Text("Release notes") }
                    }
                },
            )
        }
        is AppUpdateState.NeedsPermission -> AlertDialog(
            onDismissRequest = { updater.dismissNotice() },
            title = { Text("Allow NovelDB to install updates") },
            text = {
                Text(
                    "Android asks you once before an app may install other apps, including its own updates. " +
                        "Open the settings page, turn on \"Allow from this source\", and come back; the update continues from there.",
                )
            },
            confirmButton = { TextButton(onClick = { updater.openInstallSettings(context) }) { Text("Open settings") } },
            dismissButton = { TextButton(onClick = { updater.dismissNotice() }) { Text("Cancel") } },
        )
        is AppUpdateState.Downloading -> AlertDialog(
            onDismissRequest = { },
            title = { Text("Downloading NovelDB ${s.offer.version}") },
            text = {
                Column {
                    LinearProgressIndicator(
                        progress = { if (s.total > 0) (s.received.toFloat() / s.total).coerceIn(0f, 1f) else 0f },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text("${formatMegabytes(s.received)} of ${formatMegabytes(s.total)}", Modifier.padding(top = 8.dp))
                }
            },
            confirmButton = { },
            dismissButton = { TextButton(onClick = { updater.cancel() }) { Text("Cancel") } },
        )
        is AppUpdateState.Installing -> AlertDialog(
            onDismissRequest = { },
            title = { Text("Installing NovelDB ${s.offer.version}") },
            text = { Text("The download matches its checksum. Confirm the update in the Android screen. NovelDB closes when it is installed; open it again afterwards.") },
            confirmButton = { TextButton(onClick = { updater.dismissNotice() }) { Text("Close") } },
        )
        is AppUpdateState.Failed -> AlertDialog(
            onDismissRequest = { updater.dismissNotice() },
            title = { Text("The update was not installed") },
            text = { Text(s.text) },
            confirmButton = { TextButton(onClick = { updater.dismissNotice() }) { Text("OK") } },
        )
        else -> Unit
    }
}
