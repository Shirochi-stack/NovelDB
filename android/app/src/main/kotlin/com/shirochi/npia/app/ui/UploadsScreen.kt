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
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.DownloadManager
import com.shirochi.npia.app.FileActions
import com.shirochi.npia.upload.UploadResult
import com.shirochi.npia.upload.UploaderCatalog

@Composable
fun UploadsScreen(model: AppViewModel, topBar: TopBarState) {
    val manager = model.manager
    val history by manager.uploadHistory.collectAsStateWithLifecycle()
    var confirmClear by remember { mutableStateOf(false) }
    val hasHistory = history.isNotEmpty()
    LaunchedEffect(hasHistory) {
        val clear: @Composable () -> Unit = { TextButton(onClick = { confirmClear = true }) { Text("Clear") } }
        topBar.actions = if (hasHistory) clear else null
    }

    if (history.isEmpty()) {
        Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
            Text(
                "No uploads yet. Finished files can be uploaded from the queue, or automatically in Settings > Upload.",
                textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    } else {
        LazyColumn(Modifier.fillMaxSize()) {
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
