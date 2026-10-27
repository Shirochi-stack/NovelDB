package com.shirochi.npia.app.ui

import android.text.format.DateUtils
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.DownloadManager
import com.shirochi.npia.app.FileActions
import com.shirochi.npia.app.model.UploadState
import com.shirochi.npia.upload.UploadResult
import com.shirochi.npia.upload.UploaderCatalog
import java.io.File

@Composable
fun UploadsScreen(model: AppViewModel, topBar: TopBarState) {
    val manager = model.manager
    val history by manager.uploadHistory.collectAsStateWithLifecycle()
    val states by manager.uploadStates.collectAsStateWithLifecycle()
    // Uploads that are running or failed; finished ones are in the history below.
    val pending = states.values.filter { it.phase != UploadState.Phase.DONE }
    var confirmClear by remember { mutableStateOf(false) }
    val hasHistory = history.isNotEmpty()
    LaunchedEffect(hasHistory) {
        val clear: @Composable () -> Unit = { TextButton(onClick = { confirmClear = true }) { Text("Clear") } }
        topBar.actions = if (hasHistory) clear else null
    }

    if (history.isEmpty() && pending.isEmpty()) {
        Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
            Text(
                "No uploads yet. Finished files can be uploaded from the queue, or automatically in Settings > Upload.",
                textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    } else {
        LazyColumn(Modifier.fillMaxSize()) {
            items(pending, key = { "pending|${it.path}" }) { state ->
                Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                    Text(state.fileName, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    UploadStatus(model, state, onRetry = { manager.upload(File(state.path), state.jobId) })
                }
                HorizontalDivider()
            }
            items(history, key = { "${it.hostId}|${it.link}|${it.uploadedAt}" }) { upload ->
                UploadRow(model, upload, onRemove = { manager.removeUpload(upload) })
                HorizontalDivider()
            }
        }
    }

    if (confirmClear) {
        AlertDialog(
            onDismissRequest = { confirmClear = false },
            title = { Text("Clear upload history?") },
            text = { Text("Links stay valid on the hosts; only the list here is cleared.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmClear = false
                    manager.clearUploadHistory()
                }) { Text("Clear history") }
            },
            dismissButton = { TextButton(onClick = { confirmClear = false }) { Text("Cancel") } },
        )
    }
}

@Composable
fun UploadRow(model: AppViewModel, upload: UploadResult, onRemove: (() -> Unit)? = null) {
    val context = LocalContext.current
    val host = UploaderCatalog.uploader(upload.hostId)?.displayName ?: upload.hostId
    val now = System.currentTimeMillis()
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Text(upload.fileName, maxLines = 2)
        val expiry = upload.expiresAt?.let { expires ->
            if (expires < now) "expired" else "expires " + DateUtils.getRelativeTimeSpanString(expires, now, DateUtils.MINUTE_IN_MILLIS)
        }
        Text(
            listOfNotNull(host, DateUtils.formatDateTime(context, upload.uploadedAt, DateUtils.FORMAT_SHOW_DATE), expiry).joinToString(" \u00b7 "),
            style = MaterialTheme.typography.bodySmall,
            color = if (upload.expiresAt?.let { it < now } == true) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(Modifier.horizontalScroll(rememberScrollState())) {
            TextButton(onClick = {
                DownloadManager.copyToClipboard(context, upload.link)
                model.manager.message("Link copied")
            }) { Text("Copy link") }
            TextButton(onClick = { FileActions.shareText(context, upload.link) }) { Text("Share") }
            TextButton(onClick = { FileActions.openUrl(context, upload.link) }) { Text("Open") }
            upload.deleteLink?.let { delete ->
                if (delete.startsWith("http")) TextButton(onClick = { FileActions.openUrl(context, delete) }) { Text("Delete page") }
            }
            onRemove?.let { TextButton(onClick = it) { Text("Remove") } }
        }
    }
}

/**
 * One upload's progress, result or failure: a determinate bar and "Uploading to <host>… 42%
 * (6.3 / 15 MB)" while it runs, the link with Copy and Share when done, the reason with Retry
 * when it failed.
 */
@Composable
fun UploadStatus(model: AppViewModel, state: UploadState, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    Column(modifier.fillMaxWidth()) {
        when (state.phase) {
            UploadState.Phase.RUNNING -> {
                if (state.totalBytes > 0) {
                    LinearProgressIndicator(progress = { state.fraction }, modifier = Modifier.fillMaxWidth())
                } else {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
                Text(state.statusLine, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            UploadState.Phase.DONE -> {
                Text(state.statusLine, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Row(Modifier.horizontalScroll(rememberScrollState())) {
                    TextButton(onClick = {
                        state.link?.let { DownloadManager.copyToClipboard(context, it) }
                        model.manager.message("Link copied")
                    }) { Text("Copy") }
                    TextButton(onClick = { state.link?.let { FileActions.shareText(context, it) } }) { Text("Share") }
                    TextButton(onClick = { model.manager.dismissUpload(state.path) }) { Text("Dismiss") }
                }
            }
            UploadState.Phase.FAILED -> {
                Text(state.statusLine, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, maxLines = 3, overflow = TextOverflow.Ellipsis)
                Row(Modifier.horizontalScroll(rememberScrollState())) {
                    TextButton(onClick = onRetry) { Text("Retry") }
                    TextButton(onClick = { model.manager.dismissUpload(state.path) }) { Text("Dismiss") }
                }
            }
        }
    }
}
