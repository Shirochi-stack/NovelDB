package com.shirochi.npia.app

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.npia.NpiaAuth
import com.shirochi.npia.sources.npia.NpiaCookieSetting
import com.shirochi.npia.sources.npia.NpiaSearchOutput
import com.shirochi.npia.sources.npia.NpiaTagSearch
import com.shirochi.npia.sources.npia.TagQuery
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The npia tag search (Retrieve Novel IDs, Scrape all novels, Retrieve Top 100), the desktop
 * dialog's workers. It lives with the app, not the screen, so a search keeps running and logging
 * while the user leaves the screen and finds everything again when they come back.
 */
class TagSearchManager(
    private val scope: CoroutineScope,
    private val settings: () -> AppSettings,
    /** A context for one run; its logger is where the sign-in lines go. */
    private val contextFor: (JobLogging) -> SourceContext,
    /** Keeps the process alive while a search runs. */
    private val onStarted: () -> Unit = {},
) {
    enum class Kind { TAGS, ALL, TOP100 }

    /** What a finished search found, ready to save or add to the link cart. */
    data class Result(
        val kind: Kind,
        /** Unique novel ids, in the order found. */
        val ids: List<String>,
        /** Suggested file name for "Save list". */
        val fileName: String,
        /** The text of that file. */
        val fileText: String,
    )

    /** The newest log lines; `total` counts every line so a view can tell when something new arrived. */
    data class LogState(val lines: List<String> = emptyList(), val total: Int = 0)

    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running.asStateFlow()

    private val _log = MutableStateFlow(LogState())
    val log: StateFlow<LogState> = _log.asStateFlow()

    private val _status = MutableStateFlow("")
    /** The result summary ("Found N novel(s).") or progress text under the buttons. */
    val status: StateFlow<String> = _status.asStateFlow()

    private val _result = MutableStateFlow<Result?>(null)
    val result: StateFlow<Result?> = _result.asStateFlow()

    private val stopFlag = AtomicBoolean(false)
    private var job: Job? = null

    internal fun append(line: String) {
        _log.update { LogState((it.lines + line).takeLast(MAX_LOG_LINES), it.total + 1) }
    }

    fun clearLog() {
        _log.value = LogState()
    }

    /** Asks the running search to stop; it keeps what it found and reports "(stopped)". */
    fun stop() {
        if (_running.value) {
            stopFlag.set(true)
            append("  Stop requested...")
        }
    }

    /**
     * Starts "Retrieve Novel IDs" with the saved tag settings: a full scrape when "Scrape all
     * novels" is on, otherwise the Query Builder groups, or each selected and custom tag as its
     * own group when none are defined. Returns a message when there is nothing to search.
     */
    fun retrieve(): String? {
        if (_running.value) return null
        val s = settings()
        val npia = s.npia
        val age = npia.tagAgeFilter
        val groups: List<List<String>>? = if (npia.tagScrapeAll) {
            null
        } else {
            NpiaSearchOutput.groupsToSearch(NpiaSearchOutput.selectedForAge(npia.tagSelectedTags, age), npia.tagCustomTags, npia.tagQueryGroups)
                ?: return "Add at least one group to the query builder, or select tags directly."
        }
        val shared = s.effective(SourceId.NPIA)
        val delay = maxOf(0.0, shared.intervalMin)
        val threads = maxOf(1, shared.threads)
        val queries = npia.queryCount
        start(if (groups == null) Kind.ALL else Kind.TAGS, if (groups == null) "Scraping all novels ($queries queries)..." else "Searching...") { engine ->
            val ids: List<String>
            if (groups == null) {
                ids = engine.fetchAllNovels(delay = delay, ageFilter = age, maxQueries = queries, threads = threads)
            } else {
                ids = engine.fetchNovelsByTags(TagQuery.Groups(groups), delay = delay, ageFilter = age, threads = threads)
            }
            val stopped = stopFlag.get()
            _status.value = NpiaSearchOutput.tagSummary(ids.size, stopped)
            if (ids.isNotEmpty()) {
                val label = NpiaSearchOutput.tagLabel(groups)
                _result.value = Result(
                    if (groups == null) Kind.ALL else Kind.TAGS, ids,
                    NpiaSearchOutput.tagFileName(label), NpiaSearchOutput.idListText(ids),
                )
            }
        }
        return null
    }

    /** Starts "Retrieve Top 100": the first 100 novels of each public ranking for the saved age filter. */
    fun retrieveTop100() {
        if (_running.value) return
        val age = settings().npia.tagAgeFilter
        start(Kind.TOP100, "Fetching Top 100 rankings...") { engine ->
            append("--- Fetching Top 100 Rankings ---")
            val rankings = engine.fetchTop100Rankings(age)
            _status.value = NpiaSearchOutput.top100Summary(rankings, stopFlag.get())
            val unique = rankings.values.flatten().distinct()
            if (unique.isNotEmpty()) {
                _result.value = Result(Kind.TOP100, unique, NpiaSearchOutput.TOP100_FILE_NAME, NpiaSearchOutput.top100Text(rankings))
            }
        }
    }

    private fun start(kind: Kind, progress: String, work: suspend (NpiaTagSearch) -> Unit) {
        stopFlag.set(false)
        _result.value = null
        _status.value = progress
        _log.value = LogState()
        _running.value = true
        onStarted()
        job = scope.launch {
            try {
                val context = contextFor(JobLogging { _, message -> append(message) })
                val loginKey = NpiaAuth.prepare(context, NpiaCookieSetting { })
                work(NpiaTagSearch(context.http, loginKey, ::append, stopFlag::get))
            } catch (e: CancellationException) {
                _status.value = "Stopped."
                throw e
            } catch (e: Exception) {
                val reason = e.message ?: e.toString()
                append(if (kind == Kind.TOP100) "Top 100 retrieval failed: $reason" else "Tag retrieval failed: $reason")
                _status.value = "Error: $reason"
            } finally {
                _running.value = false
            }
        }
    }

    companion object {
        const val MAX_LOG_LINES = 1000
    }
}
