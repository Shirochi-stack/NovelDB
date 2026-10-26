package com.shirochi.npia.sources.ntk

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier

/** TODO(hard sources (Phase 5)): port from external_scraper.py _ntk_*. */
class NtkAdapter : SourceAdapter {
    override val id = SourceId.NTK
    override val tier = SourceTier.HEAVY_BROWSER

    override fun matches(url: String): Boolean = false

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo = throw SourceException.NotImplemented(id)

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        throw SourceException.NotImplemented(id)
}
