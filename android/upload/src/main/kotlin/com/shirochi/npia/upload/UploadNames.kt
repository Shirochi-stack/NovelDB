package com.shirochi.npia.upload

import java.net.URI
import java.text.Normalizer

/**
 * The names single files are uploaded under. Hosts keep only ASCII letters and digits of a file
 * name (a Korean title arrives as `epub`, `-_.epub` or `VR_.epub`), so a name that is not plain
 * ASCII is replaced by something that survives: the ASCII-safe form of the title when it keeps
 * at least `MIN_LETTERS` ASCII letters (digits alone do not make a name: "100일 후에" is not `100`),
 * else `<source>_<book id>`.
 */
object UploadNames {
    const val MIN_LETTERS = 3
    private const val MAX_BASE = 80
    private val SAFE_STEM = Regex("[A-Za-z0-9_-]{1,64}")
    private val SAFE_EXTENSION = Regex("[A-Za-z0-9]{1,8}")

    /**
     * The ASCII letters and digits of `title`, runs of anything else (spaces, punctuation, Hangul,
     * kana) joined by one `_`; null when fewer than `MIN_LETTERS` of them are letters (A-Z, a-z).
     */
    fun asciiTitle(title: String): String? {
        val out = StringBuilder()
        var pendingSeparator = false
        var letters = 0
        for (char in Normalizer.normalize(title, Normalizer.Form.NFC)) {
            if (char in 'A'..'Z' || char in 'a'..'z' || char in '0'..'9') {
                if (pendingSeparator && out.isNotEmpty()) out.append('_')
                pendingSeparator = false
                out.append(char)
                if (char !in '0'..'9') letters++
            } else {
                pendingSeparator = true
            }
        }
        if (letters < MIN_LETTERS) return null
        return out.toString().take(MAX_BASE).trimEnd('_')
    }

    /**
     * `name` with its extension split off: "책 VR.epub" to ("책 VR", "epub"), ".epub" to ("", "epub").
     * The extension is null when it is not a plain one.
     */
    fun split(name: String): Pair<String, String?> {
        val dot = name.lastIndexOf('.')
        if (dot < 0) return name to null
        val extension = name.substring(dot + 1)
        return if (SAFE_EXTENSION.matches(extension)) name.substring(0, dot) to extension else name to null
    }

    /**
     * The ASCII upload name for `name`: unchanged when it is plain printable ASCII with at least
     * `MIN_LETTERS` letters before the extension, else the ASCII-safe title, else `stem`
     * (`npia_123456`; `book` when the stem is missing or unsafe), each with the original
     * extension. A name is never just an extension: temp.sh stores ".epub" as "epub", which
     * downloads with no extension at all.
     */
    fun ascii(name: String, stem: String?): String {
        val (base, extension) = split(name)
        if (name.all { it in ' '..'~' } && base.count { it in 'A'..'Z' || it in 'a'..'z' } >= MIN_LETTERS) return name
        val chosen = asciiTitle(base) ?: stem?.takeIf { SAFE_STEM.matches(it) } ?: "book"
        return if (extension != null) "$chosen.$extension" else chosen
    }
}

/** The id and stem that name a book's uploads; see `UploadNames`. */
object UploadStem {
    private val SAFE_ID = Regex("[A-Za-z0-9_-]{1,40}")

    /**
     * The adapter's book id when it is a safe token (for npia that is the novel number), else the
     * last numeric segment of the job URL, else a short ASCII slug of its path, else "book".
     */
    fun bookId(url: String, bookId: String?): String {
        bookId?.trim()?.takeIf { SAFE_ID.matches(it) }?.let { return it }
        val path = runCatching { URI(url.trim()).path }.getOrNull().orEmpty()
        val segments = path.split('/').filter { it.isNotEmpty() }
        segments.lastOrNull { segment -> segment.all { it in '0'..'9' } }?.let { return it }
        val slug = segments.joinToString("-").lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-').take(32).trim('-')
        return slug.ifEmpty { "book" }
    }

    /** `<source>_<id>` (for example `npia_123456`); just the id when the source is unknown. */
    fun of(source: String?, id: String): String = if (source.isNullOrBlank()) id else "${source}_$id"
}
