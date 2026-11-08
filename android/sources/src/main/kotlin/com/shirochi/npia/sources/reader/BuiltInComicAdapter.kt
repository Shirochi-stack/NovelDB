package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.parseConcurrently

/** Built-in comic scrapers backed by the shared page and Python runtimes. */
internal class BuiltInComicAdapter(override val id: SourceId) : SourceAdapter {
    init { require(id == SourceId.COMIX || id == SourceId.MANGAK) }

    override val tier = SourceTier.LIGHT_BROWSER
    override val sites = if (id == SourceId.COMIX) setOf("comix.to", "comix.ws") else setOf("mangak.io")

    override fun matches(url: String): Boolean =
        if (id == SourceId.COMIX) PageScript.comixBookPages(url).isNotEmpty() else PythonEngines.mangaKMatches(url)

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo =
        ReaderSessions.openBuiltIn(url, context, id).book

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        ReaderSessions.session(book, context).chapter(ref)

    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> =
        parseConcurrently(refs, context, spaced = true) { ref -> parseChapter(ref, book, context) }
}
