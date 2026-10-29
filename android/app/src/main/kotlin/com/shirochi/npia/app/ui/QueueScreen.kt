package com.shirochi.npia.app.ui

import android.text.format.Formatter
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.FileActions
import com.shirochi.npia.app.Route
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.app.model.QueueSelection
import com.shirochi.npia.app.model.QueueView
import com.shirochi.npia.app.model.UploadState
import java.io.File

@Composable
fun QueueScreen(model: AppViewModel, topBar: TopBarState) {
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    val uploads by manager.uploadStates.collectAsStateWithLifecycle()
    val hasFinished = jobs.any { !it.state.isActive }
    val activeCount = jobs.count { it.state.isActive }
    val view = model.queueView
    var confirmStopAll by remember { mutableStateOf(false) }
    var selection by remember { mutableStateOf(QueueSelection()) }
    // Finished jobs with files left are the ones that can be shared and uploaded.
    val selectable = QueueSelection.selectable(jobs, manager.outputDirectory)
    val selectableIds = selectable.map { it.id }.toSet()
    LaunchedEffect(selectableIds) { selection = selection.pruned(selectableIds) }
    BackHandler(enabled = selection.active && view == QueueView.JOBS) { selection = selection.clear() }
    LaunchedEffect(hasFinished, activeCount, view) {
        val actions: @Composable () -> Unit = {
            if (activeCount > 0) {
                TextButton(onClick = { if (activeCount > 1) confirmStopAll = true else manager.cancelAll() }) { Text("Stop all") }
            }
            if (hasFinished) TextButton(onClick = { manager.clearFinished() }) { Text("Clear finished") }
        }
        topBar.actions = if (view == QueueView.JOBS && (hasFinished || activeCount > 0)) actions else null
    }
    if (confirmStopAll) {
        AlertDialog(
            onDismissRequest = { confirmStopAll = false },
            title = { Text("Stop all $activeCount downloads?") },
            text = { Text("Running and queued downloads are cancelled. Chapters already downloaded are kept in the cache.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmStopAll = false
                    manager.cancelAll()
                }) { Text("Stop all") }
            },
            dismissButton = { TextButton(onClick = { confirmStopAll = false }) { Text("Keep going") } },
        )
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FilterChip(selected = view == QueueView.JOBS, onClick = { model.queueView = QueueView.JOBS }, label = { Text("Jobs") })
            FilterChip(selected = view == QueueView.LOG, onClick = { model.queueView = QueueView.LOG }, label = { Text("Log") })
            Spacer(Modifier.weight(1f))
            if (view == QueueView.JOBS && !selection.active && selectable.isNotEmpty()) {
                TextButton(onClick = { selection = selection.begin() }) { Text("Select") }
            }
        }
        HorizontalDivider()
        when (view) {
            QueueView.JOBS -> QueueJobs(model, jobs, uploads, selection, { selection = it }, selectableIds, Modifier.weight(1f))
            QueueView.LOG -> QueueLogView(model, jobs, Modifier.weight(1f))
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun QueueJobs(
    model: AppViewModel,
    jobs: List<JobItem>,
    uploads: Map<String, UploadState>,
    selection: QueueSelection,
    onSelection: (QueueSelection) -> Unit,
    selectableIds: Set<String>,
    modifier: Modifier = Modifier,
) {
    val manager = model.manager
    val context = LocalContext.current
    if (jobs.isEmpty()) {
        Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("No downloads yet. Add a URL to start.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }
    Column(modifier) {
        if (selection.active) {
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (selection.count == 0) "Select finished downloads" else "${selection.count} selected",
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.titleSmall,
                )
                TextButton(
                    onClick = { onSelection(selection.selectAll(selectableIds)) },
                    enabled = selectableIds.isNotEmpty() && selection.count < selectableIds.size,
                ) { Text("Select all finished") }
                TextButton(onClick = { onSelection(selection.clear()) }) { Text("Cancel") }
            }
            HorizontalDivider()
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
            items(jobs, key = { it.id }) { job ->
                val canSelect = job.id in selectableIds
                val files = job.files(manager.outputDirectory).filter { it.exists() }
                val paths = files.map { it.absolutePath }.toSet()
                // The uploads that carry this job's files: a file of its own, or a ZIP shared with other jobs.
                val jobUploads = uploads.values.filter { state -> state.paths.any { it in paths } }.distinctBy { it.path }
                Column(
                    Modifier.fillMaxWidth().combinedClickable(
                        onClick = {
                            if (!selection.active) model.push(Route.JobDetail(job.id)) else if (canSelect) onSelection(selection.toggle(job.id))
                        },
                        onLongClick = { if (canSelect) onSelection(if (selection.active) selection.toggle(job.id) else selection.begin(job.id)) },
                    ),
                ) {
                    Row(verticalAlignment = Alignment.Top) {
                        if (selection.active) {
                            Checkbox(
                                checked = selection.isSelected(job.id),
                                onCheckedChange = { if (canSelect) onSelection(selection.toggle(job.id)) },
                                enabled = canSelect,
                                modifier = Modifier.padding(start = 4.dp),
                            )
                        }
                        Column(Modifier.weight(1f)) {
                            JobRow(job, Modifier.padding(start = if (selection.active) 4.dp else 16.dp, end = 16.dp, top = 12.dp))
                            if (!selection.active) {
                                Row(Modifier.padding(start = 8.dp).horizontalScroll(rememberScrollState())) {
                                    if (job.state.isActive) {
                                        TextButton(onClick = { manager.cancel(job.id) }) { Text("Cancel") }
                                    } else {
                                        if (job.state != JobState.FINISHED) TextButton(onClick = { manager.retry(job.id) }) { Text("Retry") }
                                        if (files.isNotEmpty()) {
                                            TextButton(onClick = { if (!FileActions.open(context, files[0])) manager.message("No app can open ${files[0].name}") }) { Text("Open") }
                                            TextButton(onClick = { FileActions.share(context, files) }) { Text("Share") }
                                            if (canSelect) {
                                                TextButton(onClick = {
                                                    model.requestUpload { if (!manager.uploadJobs(listOf(job.id))) manager.message("Nothing left to upload.") }
                                                }) { Text("Upload") }
                                            }
                                        }
                                        TextButton(onClick = { manager.remove(job.id, deleteFiles = false) }) { Text("Remove") }
                                    }
                                }
                            }
                            for (state in jobUploads) {
                                Column(Modifier.padding(start = if (selection.active) 4.dp else 16.dp, end = 16.dp, bottom = 4.dp)) {
                                    UploadStatus(model, state, onRetry = { model.requestUpload { manager.retryUpload(state) } })
                                }
                            }
                        }
                    }
                    HorizontalDivider()
                }
            }
        }
        if (selection.active) {
            val selectedFiles = QueueSelection.files(jobs, selection, manager.outputDirectory)
            Surface(tonalElevation = 3.dp, modifier = Modifier.fillMaxWidth()) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        if (selection.count == 0) "None selected" else "${selection.count} selected · ${selectedFiles.size} files",
                        Modifier.weight(1f),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    OutlinedButton(
                        enabled = selectedFiles.isNotEmpty(),
                        onClick = {
                            if (FileActions.share(context, selectedFiles)) onSelection(selection.clear()) else manager.message("Nothing left to share.")
                        },
                    ) { Text("Share") }
                    Button(
                        enabled = selectedFiles.isNotEmpty(),
                        onClick = {
                            val ids = selection.ids
                            model.requestUpload { if (!manager.uploadJobs(ids)) manager.message("Nothing left to upload.") }
                            onSelection(selection.clear())
                        },
                    ) { Text("Upload") }
                }
            }
        }
    }
}

@Composable
fun JobRow(job: JobItem, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stateMark(job.state), color = stateColor(job.state))
            Text(job.displayTitle, Modifier.weight(1f).padding(start = 8.dp), maxLines = 1, overflow = TextOverflow.Ellipsis)
            job.source?.let { Text(it.raw, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        if (job.state == JobState.RUNNING) {
            LinearProgressIndicator(progress = { job.fraction.toFloat() }, modifier = Modifier.fillMaxWidth())
        }
        Text(
            job.status,
            style = MaterialTheme.typography.bodySmall,
            color = if (job.state == JobState.FAILED) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

private fun stateMark(state: JobState) = when (state) {
    JobState.QUEUED -> "\u25cb"
    JobState.RUNNING -> "\u2193"
    JobState.FINISHED -> "\u2713"
    JobState.FAILED -> "!"
    JobState.CANCELLED -> "\u00d7"
}

@Composable
private fun stateColor(state: JobState) = when (state) {
    JobState.RUNNING -> MaterialTheme.colorScheme.primary
    JobState.FINISHED -> MaterialTheme.colorScheme.tertiary
    JobState.FAILED -> MaterialTheme.colorScheme.error
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

@Composable
fun JobDetailScreen(model: AppViewModel, id: String) {
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    val job = jobs.firstOrNull { it.id == id }
    val context = LocalContext.current
    var confirmRemove by remember { mutableStateOf(false) }

    if (job == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Removed") }
        return
    }
    val files = job.files(manager.outputDirectory)

    LazyColumn(Modifier.fillMaxSize()) {
        item {
            SectionHeader("Status")
            JobRow(job, Modifier.padding(horizontal = 16.dp))
            TextButton(onClick = { FileActions.openUrl(context, job.url) }, modifier = Modifier.padding(start = 8.dp)) {
                Text(job.url, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            job.error?.let { error ->
                SelectionContainer { Text(error, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 16.dp)) }
            }
            Row(Modifier.padding(start = 8.dp)) {
                if (job.state.isActive) {
                    TextButton(onClick = { manager.cancel(job.id) }) { Text("Cancel") }
                } else {
                    TextButton(onClick = { manager.retry(job.id) }) { Text("Retry") }
                    TextButton(onClick = { confirmRemove = true }) { Text("Remove") }
                }
            }
        }
        if (files.isNotEmpty()) {
            item { SectionHeader("Files") }
            items(files, key = { it.path }) { file -> FileRow(model, file, job.id) }
            item {
                Row(Modifier.padding(start = 8.dp)) {
                    if (files.size > 1) TextButton(onClick = { FileActions.share(context, files) }) { Text("Share all") }
                    if (job.state == JobState.FINISHED && files.any { it.exists() }) {
                        TextButton(onClick = {
                            model.requestUpload { if (!manager.uploadJobs(listOf(job.id))) manager.message("Nothing left to upload.") }
                        }) { Text(if (files.size > 1) "Upload all" else "Upload") }
                    }
                }
            }
        }
        if (job.uploads.isNotEmpty()) {
            item { SectionHeader("Links") }
            items(job.uploads) { upload -> UploadRow(model, upload) }
        }
        if (job.skipped.isNotEmpty()) {
            item { SectionHeader("Skipped chapters (${job.skipped.size})") }
            items(job.skipped) { Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp)) }
        }
        if (job.log.isNotEmpty()) {
            item { SectionHeader("Log") }
            items(job.log) { line ->
                SelectionContainer {
                    Text(line, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace, fontSize = 9.5.sp, lineHeight = 13.sp, modifier = Modifier.padding(horizontal = 16.dp))
                }
            }
        }
    }

    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("Remove this download?") },
            confirmButton = {
                TextButton(onClick = {
                    confirmRemove = false
                    manager.remove(job.id, deleteFiles = true)
                    model.pop()
                }) { Text("Remove and delete files") }
            },
            dismissButton = {
                TextButton(onClick = {
                    confirmRemove = false
                    manager.remove(job.id, deleteFiles = false)
                    model.pop()
                }) { Text("Remove, keep files") }
            },
        )
    }
}

@Composable
fun FileRow(model: AppViewModel, file: File, jobId: String?) {
    val manager = model.manager
    val context = LocalContext.current
    val uploads by manager.uploadStates.collectAsStateWithLifecycle()
    // The upload that carries this file: its own, or a ZIP it was packed into.
    val upload = uploads[file.absolutePath] ?: uploads.values.firstOrNull { file.absolutePath in it.paths }
    val saveCopy = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument(FileActions.mimeType(file))) { target ->
        if (target != null) manager.message(if (FileActions.copyTo(context, file, target)) "Saved ${file.name}" else "Could not save ${file.name}")
    }
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(file.name, Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (file.isFile) Text(Formatter.formatShortFileSize(context, file.length()), style = MaterialTheme.typography.bodySmall)
        }
        if (!file.exists()) {
            Text("Missing", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            return@Column
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            AssistChip(onClick = { if (!FileActions.open(context, file)) manager.message("No app can open ${file.name}") }, label = { Text("Open") })
            AssistChip(onClick = { FileActions.share(context, listOf(file)) }, label = { Text("Share") })
            if (file.isFile) {
                AssistChip(onClick = { saveCopy.launch(file.name) }, label = { Text("Save copy") })
                if (upload?.phase != UploadState.Phase.RUNNING) {
                    AssistChip(onClick = { model.requestUpload { manager.upload(file, jobId) } }, label = { Text("Upload") })
                }
            }
        }
        upload?.let { state ->
            UploadStatus(
                model, state,
                onRetry = { model.requestUpload { if (state.paths.size > 1) manager.retryUpload(state) else manager.upload(file, jobId, state.bookId) } },
            )
        }
    }
}
