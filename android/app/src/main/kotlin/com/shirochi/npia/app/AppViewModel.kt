package com.shirochi.npia.app

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.lifecycle.AndroidViewModel
import com.shirochi.npia.core.SourceId

enum class Tab(val title: String) { ADD("Add"), QUEUE("Queue"), UPLOADS("Uploads"), SETTINGS("Settings") }

/** Screens pushed on top of a tab's root. */
sealed class Route(val title: String) {
    data class JobDetail(val id: String) : Route("Download")
    data object General : Route("General")
    data object Overrides : Route("Per-source overrides")
    data class OverrideForm(val source: SourceId) : Route(source.raw)
    data object SourceOptions : Route("Source options")
    data object Npia : Route("npia")
    data object Images : Route("Images")
    data object Text : Route("Text and PDF")
    data object Upload : Route("Upload")
    data object Background : Route("Screen and background")
    data object Transfer : Route("Import / export")
}

/** Navigation state that survives rotation; the data lives in `DownloadManager`. */
class AppViewModel(application: Application) : AndroidViewModel(application) {
    val manager: DownloadManager get() = getApplication<NpiaApplication>().downloads

    var tab by mutableStateOf(Tab.ADD)
    private val stacks: Map<Tab, SnapshotStateList<Route>> = Tab.entries.associateWith { mutableStateListOf() }

    /** Text handed over by the share sheet, consumed by the Add screen. */
    var sharedText by mutableStateOf<String?>(null)

    fun stack(tab: Tab): List<Route> = stacks.getValue(tab)

    fun push(route: Route) {
        stacks.getValue(tab).add(route)
    }

    fun pop(): Boolean {
        val stack = stacks.getValue(tab)
        if (stack.isEmpty()) return false
        stack.removeAt(stack.lastIndex)
        return true
    }

    fun select(next: Tab) {
        // Tapping the current tab returns to its root.
        if (next == tab) stacks.getValue(tab).clear()
        tab = next
    }
}
