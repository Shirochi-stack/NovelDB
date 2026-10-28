package com.shirochi.npia.sources.engines

import java.net.URI

/**
 * The page patterns of a userscript header: the `// @match`, `// @include` and `// @exclude` lines
 * of the novel-downloader rules bundle. A page counts when some `@match` or `@include` accepts it
 * and no `@exclude` does, which is how the userscript manager decides where the script runs.
 *
 * Match patterns follow Greasemonkey: `*` as the scheme is http or https, `*` elsewhere is any run
 * of characters (in the host it stops at a slash), and a host that starts with `*.` is the site
 * and all its subdomains. The path is compared with its query; the fragment and port are ignored.
 * `@include` and `@exclude` may also be `/regular expressions/` tested against the same address.
 * Hosts in the bundle are partly written as `\uXXXX` escapes; they are decoded first.
 */
class UserScriptPatterns private constructor(private val allow: List<Regex>, private val deny: List<Regex>) {
    /** Number of `@match` and `@include` patterns that compiled. */
    val allowCount: Int get() = allow.size

    /** Number of `@exclude` patterns that compiled. */
    val denyCount: Int get() = deny.size

    fun accepts(url: String): Boolean {
        val target = target(url) ?: return false
        return allow.any { it.containsMatchIn(target) } && deny.none { it.containsMatchIn(target) }
    }

    companion object {
        private val line = Regex("""(?m)^//\s*@(match|include|exclude)\s+(\S.*?)\s*$""")
        private const val END = "==/UserScript=="

        /** Reads the header of `script` (the text up to its `==/UserScript==` line). */
        fun parse(script: String): UserScriptPatterns {
            val end = script.indexOf(END)
            val header = if (end < 0) script.take(200_000) else script.substring(0, end)
            val allow = ArrayList<Regex>()
            val deny = ArrayList<Regex>()
            for (match in line.findAll(header)) {
                val regex = compile(match.groupValues[2]) ?: continue
                if (match.groupValues[1] == "exclude") deny.add(regex) else allow.add(regex)
            }
            return UserScriptPatterns(allow, deny)
        }

        /** One pattern as a regular expression over `scheme://host/path?query`; null when it cannot be one. */
        fun compile(raw: String): Regex? = runCatching {
            val pattern = decode(raw.trim())
            val last = pattern.lastIndexOf('/')
            if (pattern.startsWith("/") && last > 0 && pattern.substring(last + 1).all { it in "ims" }) {
                val options = pattern.substring(last + 1).mapNotNull {
                    when (it) {
                        'i' -> RegexOption.IGNORE_CASE
                        'm' -> RegexOption.MULTILINE
                        else -> RegexOption.DOT_MATCHES_ALL
                    }
                }.toSet()
                return@runCatching Regex(pattern.substring(1, last), options)
            }
            val separator = pattern.indexOf("://")
            if (separator < 0) return@runCatching null
            val scheme = pattern.substring(0, separator)
            val rest = pattern.substring(separator + 3)
            val slash = rest.indexOf('/')
            val host = if (slash < 0) rest else rest.substring(0, slash)
            val path = if (slash < 0) "/" else rest.substring(slash)
            val schemeRegex = if (scheme == "*") "https?" else glob(scheme, ".*")
            val hostRegex = when {
                host == "*" -> "[^/]*"
                host.startsWith("*.") -> """(?:[^/]*\.)?""" + glob(host.drop(2), "[^/]*")
                else -> glob(host, "[^/]*")
            }
            Regex("^$schemeRegex://$hostRegex${glob(path, ".*")}$", RegexOption.IGNORE_CASE)
        }.getOrNull()

        /** `text` with each backslash-u escape of four hex digits replaced by its character. */
        fun decode(text: String): String {
            if (!text.contains(92.toChar())) return text
            val out = StringBuilder(text.length)
            var i = 0
            while (i < text.length) {
                val c = text[i]
                val code = if (c == 92.toChar() && i + 5 < text.length && text[i + 1] == 'u') {
                    text.substring(i + 2, i + 6).toIntOrNull(16)
                } else {
                    null
                }
                if (code != null) {
                    out.append(code.toChar())
                    i += 6
                } else {
                    out.append(c)
                    i++
                }
            }
            return out.toString()
        }

        /** Literal text between `*` wildcards, each wildcard becoming `any`. */
        private fun glob(text: String, any: String): String =
            text.split('*').joinToString(any) { if (it.isEmpty()) "" else Regex.escape(it) }

        private fun target(url: String): String? {
            val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return null
            val scheme = uri.scheme?.lowercase() ?: return null
            val host = uri.host?.lowercase() ?: return null
            val path = uri.rawPath.orEmpty().ifEmpty { "/" }
            val query = uri.rawQuery?.let { "?$it" }.orEmpty()
            return "$scheme://$host$path$query"
        }
    }
}
