package com.shirochi.npia.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.DownloadManager
import com.shirochi.npia.app.model.CartItem
import com.shirochi.npia.core.NpiaSettings
import com.shirochi.npia.sources.npia.NpiaCommonTags
import com.shirochi.npia.sources.npia.NpiaSearchOutput

/** Above this many ids, "Add to link cart" asks first. */
const val CART_CONFIRM_THRESHOLD = 200

/**
 * npia tag search: pick tags or build a query, scrape all novels, or read the Top 100 rankings,
 * with a live log. The search runs in `TagSearchManager`, so it continues when this screen is left.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TagSearchScreen(model: AppViewModel) {
    val manager = model.manager
    val search = manager.tagSearch
    val context = LocalContext.current
    val settings by manager.settings.collectAsStateWithLifecycle()
    val running by search.running.collectAsStateWithLifecycle()
    val log by search.log.collectAsStateWithLifecycle()
    val status by search.status.collectAsStateWithLifecycle()
    val result by search.result.collectAsStateWithLifecycle()
    val npia = settings.npia
    fun set(change: (NpiaSettings) -> NpiaSettings) = manager.updateSettings { it.copy(npia = change(it.npia)) }

    var confirmCart by remember { mutableStateOf(false) }
    var pendingSave by remember { mutableStateOf<String?>(null) }
    val saver = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
        val text = pendingSave
        pendingSave = null
        if (uri == null || text == null) return@rememberLauncherForActivityResult
        val ok = runCatching { context.contentResolver.openOutputStream(uri, "wt")?.use { it.write(text.toByteArray()) } != null }.getOrDefault(false)
        manager.message(if (ok) "Saved the list." else "Could not save the list.")
    }

    fun addToCart(ids: List<String>) {
        model.cart.addAll(ids.map { CartItem(NpiaSearchOutput.novelUrl(it), "npia $it", "npia") })
        manager.message("Added ${ids.size} novels to the link cart")
    }

    val tagsLocked = running || npia.tagScrapeAll
    val selected = npia.tagSelectedTags
    val customTags = NpiaSearchOutput.customTags(npia.tagCustomTags)

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
        SectionHeader("Age")
        ChoiceRow(
            "Age", npia.tagAgeFilter, listOf("", "15", "19"),
            { when (it) { "" -> "All"; "15" -> "Non-adult only"; else -> "Adult only" } },
            { age ->
                // The adult tag cannot be searched with an age filter, so it is unticked like on the desktop.
                set { it.copy(tagAgeFilter = age, tagSelectedTags = NpiaSearchOutput.selectedForAge(it.tagSelectedTags, age)) }
            },
            enabled = !running,
        )

        SectionHeader("Tags")
        ChipFlow {
            NpiaCommonTags.ALL.forEach { common ->
                val adultLocked = common.tag == NpiaCommonTags.ADULT_TAG && npia.tagAgeFilter.isNotEmpty()
                ToggleChip(common.display, common.tag in selected, enabled = !tagsLocked && !adultLocked) { on ->
                    set { it.copy(tagSelectedTags = if (on) it.tagSelectedTags + common.tag else it.tagSelectedTags - common.tag) }
                }
            }
        }
        OutlinedTextField(
            value = npia.tagCustomTags, onValueChange = { v -> set { it.copy(tagCustomTags = v) } },
            label = { Text("Custom tags (comma-separated)") }, enabled = !tagsLocked,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )

        SectionHeader("Query Builder")
        Footnote("Groups are AND'd, tags within a group are OR'd. Without groups, every selected or custom tag is its own group.")
        Row(Modifier.padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton(enabled = !tagsLocked, onClick = {
                val group = NpiaSearchOutput.selectionTags(selected, npia.tagCustomTags)
                if (group.isEmpty()) {
                    manager.message("Select at least one tag to add as a group.")
                } else {
                    // Tags are unticked after adding, like on the desktop.
                    set { it.copy(tagQueryGroups = it.tagQueryGroups + listOf(group), tagSelectedTags = emptyList(), tagCustomTags = "") }
                }
            }) { Text("Add Selected as Group") }
            TextButton(enabled = !running && npia.tagQueryGroups.isNotEmpty(), onClick = { set { it.copy(tagQueryGroups = emptyList()) } }) { Text("Clear All") }
        }
        if (npia.tagQueryGroups.isEmpty()) {
            Footnote("No groups defined. Select tags and tap Add Selected as Group.")
        }
        npia.tagQueryGroups.forEachIndexed { index, group ->
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(if (index == 0) "     " else "AND ", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("Group ${index + 1}: ${group.joinToString(" OR ")}", Modifier.weight(1f))
                IconButton(enabled = !running, onClick = { set { it.copy(tagQueryGroups = it.tagQueryGroups.filterIndexed { i, _ -> i != index }) } }) {
                    Icon(Icons.Filled.Close, contentDescription = "Remove group ${index + 1}")
                }
            }
        }

        SectionHeader("Scrape all novels")
        SwitchRow("Scrape all novels", npia.tagScrapeAll, { v -> set { it.copy(tagScrapeAll = v) } }, enabled = !running)
        if (npia.tagScrapeAll) {
            FieldFlow {
                FieldGroup {
                    MiniLabel("Search queries")
                    MiniStepper(npia.queryCount, 1..NpiaSettings.MAX_QUERIES) { v -> set { it.copy(scrapeMaxQueries = v) } }
                    MiniNumberField(npia.queryCount.toString(), "${NpiaSettings.MAX_QUERIES}", decimal = false, width = 64) { text ->
                        text.trim().toIntOrNull()?.let { v -> set { it.copy(scrapeMaxQueries = v.coerceIn(1, NpiaSettings.MAX_QUERIES)) } }
                    }
                }
            }
            Footnote("More queries = better coverage but slower. ${NpiaSettings.MAX_QUERIES} = full current tag + sweep coverage.")
        }

        FlowRow(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(enabled = !running, onClick = { search.retrieve()?.let { manager.message(it) } }) { Text("Retrieve Novel IDs") }
            OutlinedButton(enabled = !running, onClick = { search.retrieveTop100() }) { Text("Retrieve Top 100") }
            OutlinedButton(enabled = running, onClick = { search.stop() }) { Text("Stop") }
        }

        if (status.isNotEmpty()) {
            Text(status, Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodyMedium)
        }
        result?.let { found ->
            Row(Modifier.padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = {
                    pendingSave = found.fileText
                    saver.launch(found.fileName)
                }) { Text("Save list") }
                TextButton(onClick = {
                    if (found.ids.size > CART_CONFIRM_THRESHOLD) confirmCart = true else addToCart(found.ids)
                }) { Text("Add to link cart") }
            }
        }

        SectionHeader("Log")
        LogPanel(log.lines, log.total)
        Row(Modifier.padding(horizontal = 8.dp)) {
            TextButton(enabled = log.lines.isNotEmpty(), onClick = {
                DownloadManager.copyToClipboard(context, log.lines.joinToString("\n"))
                manager.message("Log copied")
            }) { Text("Copy log") }
            TextButton(enabled = !running && log.lines.isNotEmpty(), onClick = { search.clearLog() }) { Text("Clear log") }
        }
        Footnote("The search keeps running if you leave this screen. Stop keeps what was found so far.")
    }

    val pendingResult = result
    if (confirmCart && pendingResult != null) {
        AlertDialog(
            onDismissRequest = { confirmCart = false },
            title = { Text("Add ${pendingResult.ids.size} novels to the link cart?") },
            text = { Text("The Add tab then lists every one of them for download.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmCart = false
                    addToCart(pendingResult.ids)
                }) { Text("Add them") }
            },
            dismissButton = { TextButton(onClick = { confirmCart = false }) { Text("Cancel") } },
        )
    }
}

/** The log in a fixed-height monospace box that follows the newest line. */
@Composable
private fun LogPanel(lines: List<String>, total: Int) {
    val state = rememberLazyListState()
    LaunchedEffect(total) {
        if (lines.isNotEmpty()) state.scrollToItem(lines.lastIndex)
    }
    Surface(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp).height(240.dp),
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
    ) {
        SelectionContainer {
            LazyColumn(Modifier.fillMaxWidth().padding(8.dp), state = state) {
                items(lines.size) { index ->
                    Text(lines[index], fontFamily = FontFamily.Monospace, fontSize = 11.sp, lineHeight = 14.sp)
                }
            }
        }
    }
}
