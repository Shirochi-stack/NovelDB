package com.shirochi.npia.app.ui

import android.text.format.Formatter
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.FileActions
import com.shirochi.npia.app.Route
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import java.io.File

@Composable
fun QueueScreen(model: AppViewModel, topBar: TopBarState) {
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    val hasFinished = jobs.any { !it.state.isActive }
    LaunchedEffect(hasFinished) {
        val clear: @Composable () -> Unit = { TextButton(onClick = { manager.clearFinished() }) { Text("Clear finished") } }
        topBar.actions = if (hasFinished) clear else null
    }

    if (jobs.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("No downloads yet. Add a URL to start.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }
    LazyColumn(Modifier.fillMaxSize()) {
        items(jobs, key = { it.id }) { job ->
            Column(Modifier.fillMaxWidth().clickable { model.push(Route.JobDetail(job.id)) }) {
                JobRow(job, Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp))
                Row(Modifier.padding(start = 8.dp).horizontalScroll(rememberScrollState())) {
                    if (job.state.isActive) {
                        TextButton(onClick = { manager.cancel(job.id) }) { Text("Cancel") }
                    } else {
                        if (job.state != JobState.FINISHED) TextButton(onClick = { manager.retry(job.id) }) { Text("Retry") }
                        val context = LocalContext.current
                        val files = job.files(manager.outputDirectory).filter { it.exists() }
                        if (files.isNotEmpty()) {
                            TextButton(onClick = { if (!FileActions.open(context, files[0])) manager.message("No app can open ${files[0].name}") }) { Text("Open") }
                            TextButton(onClick = { FileActions.share(context, files) }) { Text("Share") }
                        }
                        TextButton(onClick = { manager.remove(job.id, deleteFiles = false) }) { Text("Remove") }
                    }
                }
                HorizontalDivider()
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
            if (files.size > 1) {
                item { TextButton(onClick = { FileActions.share(context, files) }, modifier = Modifier.padding(start = 8.dp)) { Text("Share all") } }
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
            items(job.log.takeLast(200)) { line ->
                SelectionContainer {
                    Text(line, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace, modifier = Modifier.padding(horizontal = 16.dp))
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
    val uploading by manager.uploadsInFlight.collectAsStateWithLifecycle()
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
                if (file.absolutePath in uploading) {
                    CircularProgressIndicator(Modifier.padding(4.dp))
                } else {
                    AssistChip(onClick = { manager.upload(file, jobId) }, label = { Text("Upload") })
                }
            }
        }
    }
}
