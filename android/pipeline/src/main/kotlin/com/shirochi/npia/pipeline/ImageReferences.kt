package com.shirochi.npia.pipeline

/** Rewrites image references in chapter HTML after recompression changes a file name. */
object ImageReferences {
    /** `name.png` -> `name.webp`; names without an extension get one appended. */
    fun renamed(filename: String, extension: String): String {
        val slash = filename.lastIndexOf('/') + 1
        val dot = filename.lastIndexOf('.')
        return if (dot > slash) filename.substring(0, dot) + "." + extension else "$filename.$extension"
    }

    /** Extension of the last path segment, lower-cased, or "" when it has none. */
    fun extension(filename: String): String {
        val slash = filename.lastIndexOf('/') + 1
        val dot = filename.lastIndexOf('.')
        return if (dot > slash) filename.substring(dot + 1).lowercase() else ""
    }

    private val attribute = Regex("""((?:src|href|xlink:href)\s*=\s*)(["'])(.*?)\2""", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))

    /**
     * Replaces `src`/`href` values that equal an old name, or end with `/old name`
     * (e.g. `../images/1.png` when the ref is `images/1.png` or `1.png`).
     */
    fun rewrite(html: String, renames: Map<String, String>): String = rewriteValues(html) { value ->
        renames[value] ?: renames.entries.firstOrNull { value.endsWith("/" + it.key) }?.let { (old, new) ->
            value.dropLast(old.length) + new
        }
    }

    /** Calls `transform` for every `src`/`href` value; a non-null result replaces it. */
    fun rewriteValues(html: String, transform: (String) -> String?): String =
        attribute.replace(html) { match ->
            val value = match.groupValues[3]
            val replacement = transform(value) ?: return@replace match.value
            match.groupValues[1] + match.groupValues[2] + replacement + match.groupValues[2]
        }
}
