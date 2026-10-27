package com.shirochi.npia.sources.npia

/**
 * How the tag search turns what the user picked into a query, and the results into the text
 * files and summaries of the desktop dialog (`action_tag_retrieval`, `_tag_retrieval_worker` and
 * `_top100_worker` in `gui.py`).
 */
object NpiaSearchOutput {
    const val TOP100_FILE_NAME = "top100_rankings.txt"

    /** "Custom tags (comma-separated)": trimmed, without empty entries. */
    fun customTags(text: String): List<String> = text.split(",").map { it.trim() }.filter { it.isNotEmpty() }

    /** The tags a group made of the current selection holds: ticked tags first, then the custom ones, without repeats. */
    fun selectionTags(selected: List<String>, customText: String): List<String> = (selected + customTags(customText)).distinct()

    /**
     * The groups a search runs. With groups defined they are used as they are; otherwise every
     * selected or custom tag becomes a group of its own, so all of them are AND'd. Null when there
     * is nothing to search.
     */
    fun groupsToSearch(selected: List<String>, customText: String, groups: List<List<String>>): List<List<String>>? {
        if (groups.isNotEmpty()) return groups
        return selectionTags(selected, customText).map { listOf(it) }.takeIf { it.isNotEmpty() }
    }

    /** Selected tags with the adult tag dropped when the age filter is not "All" (the checkbox is disabled then). */
    fun selectedForAge(selected: List<String>, ageFilter: String): List<String> =
        if (ageFilter.isEmpty()) selected else selected.filter { it != NpiaCommonTags.ADULT_TAG }

    // Files

    /**
     * The file name stem: "all_novels" for a full scrape, otherwise the groups joined with "+",
     * each group's tags joined with "_OR_", and non-ASCII tags replaced by their English name
     * with spaces as underscores (the tag itself when it has none).
     */
    fun tagLabel(groups: List<List<String>>?): String {
        if (groups == null) return "all_novels"
        return groups.joinToString("+") { group ->
            group.joinToString("_OR_") { tag ->
                if (tag.all { it.code < 128 }) tag else (NpiaCommonTags.englishName(tag) ?: tag).replace(" ", "_")
            }
        }
    }

    /** `tag_<label>.txt`, with characters a file name cannot hold replaced (an English tag name can contain "/"). */
    fun tagFileName(label: String): String = safeFileName("tag_$label.txt")

    fun safeFileName(name: String): String = name.replace(Regex("[\\\\/:*?\"<>|\\u0000-\\u001f]"), "_")

    /** One id per line. */
    fun idListText(ids: List<String>): String = ids.joinToString("") { "$it\n" }

    /** Sections "# <Label Title-cased> Top 100", the ids, then a blank line, for every ranking. */
    fun top100Text(rankings: Map<String, List<String>>): String = buildString {
        for ((label, ids) in rankings) {
            append("# ${titleCase(label)} Top 100\n")
            ids.forEach { append(it).append('\n') }
            append('\n')
        }
    }

    /** Python's `str.title()`: the first letter of every run of letters upper-cased, the others lower-cased. */
    fun titleCase(text: String): String {
        val out = StringBuilder()
        var inWord = false
        for (ch in text) {
            if (ch.isLetter()) {
                out.append(if (inWord) ch.lowercaseChar() else ch.uppercaseChar())
                inWord = true
            } else {
                out.append(ch)
                inWord = false
            }
        }
        return out.toString()
    }

    // Summaries

    fun tagSummary(count: Int, stopped: Boolean): String = "Found $count novel(s)." + if (stopped) " (stopped)" else ""

    /** "Top 100: N unique novels", then one line per audience such as "  all: daily: 100, weekly: 100, monthly: 100". */
    fun top100Summary(rankings: Map<String, List<String>>, stopped: Boolean): String {
        val unique = rankings.values.flatten().toSet().size
        val lines = mutableListOf("Top 100: $unique unique novels")
        val byAudience = LinkedHashMap<String, MutableList<String>>()
        for ((label, ids) in rankings) {
            val parts = label.split(" ").filter { it.isNotEmpty() }
            val audience = if (parts.size > 1) parts.last() else "all"
            val period = parts.firstOrNull() ?: label
            byAudience.getOrPut(audience) { mutableListOf() }.add("$period: ${ids.size}")
        }
        for ((audience, periods) in byAudience) lines.add("  $audience: ${periods.joinToString(", ")}")
        if (stopped) lines.add("(stopped)")
        return lines.joinToString("\n")
    }

    /** The cart URL of a found novel id. */
    fun novelUrl(id: String): String = NpiaTagSearch.novelUrl(id)
}
