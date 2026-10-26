package com.shirochi.npia.sources.nweb

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier

/** TODO(http sources (C)): port from external_scraper.py _nweb_*; iOS NpiaSources/Nweb. */
class NwebAdapter : SourceAdapter {
    override val id = SourceId.NWEB
    override val tier = SourceTier.HTTP

    override fun matches(url: String): Boolean = false

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo = throw SourceException.NotImplemented(id)

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        throw SourceException.NotImplemented(id)
}
