package com.shirochi.npia.app.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
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
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.DownloadManager
import com.shirochi.npia.app.FileActions
import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.LogEntry
import com.shirochi.npia.app.model.LogFilter
import com.shirochi.npia.app.model.LogLines
import com.shirochi.npia.core.LogLevel
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.sample
import java.io.File

/**
 * Queue, Log: every job's lines in time order. A drop-down picks All jobs or one job, Details
 * shows the debug-level lines, and the list follows new lines until the user scrolls up.
 */
@OptIn(FlowPreview::class)
@Composable
fun QueueLogView(model: AppViewModel, jobs: List<JobItem>, modifier: Modifier = Modifier) {
    val manager = model.manager
    val context = LocalContext.current
    val log = manager.queueLog
    // New lines can arrive many times a second; the list is refreshed a few times a second.
    val tick by remember { log.version.sample(250) }.collectAsStateWithLifecycle(log.version.value)
    val filterId = model.logJobFilter?.takeIf { id -> jobs.any { it.id == id } }
    val details = model.logDetails
    val entries = remember(tick, filterId, details) { LogFilter.apply(log.snapshot(), filterId, details) }
    val titles = remember(jobs) { jobs.associate { it.id to it.displayTitle } }
    var picking by remember { mutableStateOf(false) }
    var confirmClear by remember { mutableStateOf(false) }

    val listState = rememberLazyListState()
    // Follows the newest line unless the user scrolled away from the bottom (checked when a scroll ends).
    var following by remember { mutableStateOf(true) }
    LaunchedEffect(listState) {
        // The first value is the initial "not scrolling"; only scrolls that really happened count.
        snapshotFlow { listState.isScrollInProgress }.drop(1).filter { !it }.collect { following = !listState.canScrollForward }
    }
    LaunchedEffect(entries.lastOrNull()?.seq, entries.size, following) {
        if (following && entries.isNotEmpty()) listState.scrollToItem(entries.lastIndex)
    }

    Column(modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 8.dp).horizontalScroll(rememberScrollState()),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Box {
                TextButton(onClick = { picking = true }) {
                    Text(
                        filterId?.let { LogLines.shortTitle(titles[it].orEmpty(), 22) } ?: "All jobs",
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(" ▾")
                }
                DropdownMenu(expanded = picking, onDismissRequest = { picking = false }) {
                    DropdownMenuItem(text = { Text("All jobs") }, onClick = {
                        picking = false
                        model.logJobFilter = null
                    })
                    jobs.forEach { job ->
                        DropdownMenuItem(text = { Text(LogLines.shortTitle(job.displayTitle, 40)) }, onClick = {
                            picking = false
                            model.logJobFilter = job.id
                        })
                    }
                }
            }
            FilterChip(selected = details, onClick = { model.logDetails = !details }, label = { Text("Details") })
            TextButton(
                enabled = entries.isNotEmpty(),
                onClick = {
                    DownloadManager.copyToClipboard(context, LogLines.text(entries, titles))
                    manager.message("Copied ${entries.size} log lines")
                },
            ) { Text("Copy") }
            TextButton(enabled = entries.isNotEmpty(), onClick = { shareLog(context, manager::message, LogLines.text(entries, titles)) }) { Text("Share") }
            TextButton(enabled = log.size > 0, onClick = { confirmClear = true }) { Text("Clear") }
        }
        HorizontalDivider()
        if (entries.isEmpty()) {
            Box(Modifier.weight(1f).fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) {
                Text(
                    if (log.size == 0) "No log lines yet. Downloads write their progress, retries and errors here." else "No lines match this filter.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            Box(Modifier.weight(1f).fillMaxWidth()) {
                LazyColumn(Modifier.fillMaxSize(), state = listState) {
                    items(entries.size, key = { entries[it].seq }) { index -> LogLine(entries[index], titles) }
                }
                if (!following) {
                    TextButton(
                        onClick = { following = true },
                        modifier = Modifier.align(Alignment.BottomEnd).padding(8.dp),
                    ) { Text("Latest ↓") }
                }
            }
        }
    }

    if (confirmClear) {
        val one = filterId?.let { titles[it] }
        AlertDialog(
            onDismissRequest = { confirmClear = false },
            title = { Text(if (one != null) "Clear this job's log?" else "Clear the whole log?") },
            text = { Text("Only the log lines are removed; downloads and files stay.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmClear = false
                    log.clear(filterId)
                }) { Text("Clear") }
            },
            dismissButton = { TextButton(onClick = { confirmClear = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun LogLine(entry: LogEntry, titles: Map<String, String>) {
    val color = when (entry.level) {
        LogLevel.ERROR -> MaterialTheme.colorScheme.error
        LogLevel.WARNING -> MaterialTheme.colorScheme.tertiary
        LogLevel.DEBUG -> MaterialTheme.colorScheme.onSurfaceVariant
        LogLevel.INFO -> Color.Unspecified
    }
    val title = LogLines.shortTitle(entry.jobId?.let { titles[it] } ?: entry.job.ifEmpty { "queue" })
    Text(
        "${LogLines.clock(entry.time)} ${LogLines.tag(entry.level)} $title: ${entry.message}",
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 1.dp),
        style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace, fontSize = 9.5.sp, lineHeight = 13.sp),
        color = color,
    )
}

/** Shares log text; a long log goes as a file because intents cannot carry that much text. */
private fun shareLog(context: android.content.Context, message: (String) -> Unit, text: String) {
    val shared = if (text.length <= 60_000) {
        FileActions.shareText(context, text)
    } else {
        val file = File(context.filesDir, "shared/queue-log.txt")
        runCatching {
            file.parentFile?.mkdirs()
            file.writeText(text)
        }.isSuccess && FileActions.share(context, listOf(file))
    }
    if (!shared) message("Could not share the log.")
}
