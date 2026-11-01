package com.shirochi.npia.sources.mpia

import com.shirochi.npia.core.SourceId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MpiaAdapterTest {
    private val adapter = MpiaAdapter()

    // The host is read from the escaped constant, so the provider name stays out of this file too.
    private val host = MpiaSite.DOMAIN

    @Test
    fun matchesDetailViewerAndLegacyUrls() {
        assertTrue(adapter.matches("https://www.$host/novel/detail/12345"))
        assertTrue(adapter.matches("https://$host/novel/detail/12345/"))
        assertTrue(adapter.matches("https://www.$host/novel/viewer/12345/67890"))
        assertTrue(adapter.matches("https://novel.$host/12345"))
        assertTrue(adapter.matches("https://novel.$host/12345/page/2"))
    }

    @Test
    fun rejectsOtherUrls() {
        assertFalse(adapter.matches("https://www.$host/"))
        assertFalse(adapter.matches("https://www.$host/novel/detail/"))
        assertFalse(adapter.matches("https://example.com/novel/detail/1"))
        assertFalse(adapter.matches("ftp://www.$host/novel/detail/1"))
        assertFalse(adapter.matches("not a url"))
    }

    @Test
    fun listsItsSiteAndOwnsItsSubdomains() {
        assertEquals(setOf(host), adapter.sites)
        assertTrue(adapter.ownsSite(host))
        assertTrue(adapter.ownsSite("www.$host"))
        assertTrue(adapter.ownsSite("novel.$host"))
        assertFalse(adapter.ownsSite("example.com"))
        assertEquals(SourceId.MPIA, adapter.id)
    }

    @Test
    fun canonicalUrlPointsAtTheBookDetailPage() {
        val detail = MpiaSite.bookUrl("12345")
        assertEquals(detail, adapter.canonicalUrl("https://www.$host/novel/viewer/12345/67890"))
        assertEquals(detail, adapter.canonicalUrl("https://novel.$host/12345/neSrl/67890"))
        assertEquals(detail, adapter.canonicalUrl("https://www.$host/novel/detail/12345"))
    }
}
