package com.shirochi.npia.sources.npia

/** Small regex helpers so the npia parser can keep the desktop patterns verbatim. */
internal object NpiaRx {
    private val cache = java.util.concurrent.ConcurrentHashMap<Pair<String, Set<RegexOption>>, Regex>()

    fun compile(pattern: String, options: Set<RegexOption> = emptySet()): Regex =
        cache.getOrPut(pattern to options) { Regex(pattern, options) }

    val I = setOf(RegexOption.IGNORE_CASE)
    val S = setOf(RegexOption.DOT_MATCHES_ALL)
    val IS = setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)

    /** Capture groups of every match; missing groups become "". */
    fun all(pattern: String, text: String, options: Set<RegexOption> = emptySet()): List<List<String>> =
        compile(pattern, options).findAll(text).map { it.groupValues }.toList()

    fun first(pattern: String, text: String, options: Set<RegexOption> = emptySet()): List<String>? =
        compile(pattern, options).find(text)?.groupValues

    fun matches(pattern: String, text: String, options: Set<RegexOption> = emptySet()): List<MatchResult> =
        compile(pattern, options).findAll(text).toList()

    fun contains(pattern: String, text: String, options: Set<RegexOption> = emptySet()): Boolean =
        compile(pattern, options).containsMatchIn(text)

    /** Replaces every match with `with` taken literally (no `$` groups). */
    fun replace(pattern: String, text: String, with: String, options: Set<RegexOption> = emptySet()): String =
        compile(pattern, options).replace(text) { with }

    /** Replaces each match with the transform's result (groups as in `all`). */
    fun replace(pattern: String, text: String, options: Set<RegexOption> = emptySet(), transform: (List<String>) -> String): String =
        compile(pattern, options).replace(text) { transform(it.groupValues) }
}

internal object NpiaEntities {
    private val named = mapOf(
        "amp" to "&", "lt" to "<", "gt" to ">", "quot" to "\"", "apos" to "'", "nbsp" to "\u00a0",
        "middot" to "\u00b7", "hellip" to "\u2026", "lsquo" to "\u2018", "rsquo" to "\u2019",
        "ldquo" to "\u201c", "rdquo" to "\u201d", "ndash" to "\u2013", "mdash" to "\u2014",
        "copy" to "\u00a9", "reg" to "\u00ae", "trade" to "\u2122",
    )

    /** Python's `html.unescape` for the entities pages actually use. */
    fun decode(text: String): String {
        if (!text.contains('&')) return text
        return NpiaRx.replace("&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z]+);", text) { groups ->
            val body = groups[1]
            val code = when {
                body.startsWith("#x") || body.startsWith("#X") -> body.drop(2).toIntOrNull(16)
                body.startsWith("#") -> body.drop(1).toIntOrNull()
                else -> null
            }
            when {
                code != null && Character.isValidCodePoint(code) -> String(Character.toChars(code))
                code == null && !body.startsWith("#") -> named[body] ?: groups[0]
                else -> groups[0]
            }
        }
    }

    /** Python's `html.escape(text, quote=True)`. */
    fun escape(text: String): String {
        val out = StringBuilder(text.length)
        for (ch in text) {
            when (ch) {
                '&' -> out.append("&amp;")
                '<' -> out.append("&lt;")
                '>' -> out.append("&gt;")
                '"' -> out.append("&quot;")
                '\'' -> out.append("&#x27;")
                else -> out.append(ch)
            }
        }
        return out.toString()
    }
}
