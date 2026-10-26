package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier

/** TODO(npia core (A)): port from downloader_core.py, npia_auth.py; iOS NpiaSources/Npia. */
class NpiaAdapter : SourceAdapter {
    override val id = SourceId.NPIA
    override val tier = SourceTier.HTTP

    override fun matches(url: String): Boolean = false

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo = throw SourceException.NotImplemented(id)

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        throw SourceException.NotImplemented(id)
}
