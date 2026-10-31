package com.shirochi.npia.sources.ntk

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NtkAdapterTest {
    private val adapter = NtkAdapter()

    @Test
    fun recognisesNtkHostsAndPaths() {
        assertTrue(adapter.matches("https://ntk01.com/novel/12345"))
        assertTrue(adapter.matches("https://newtoki468.com/webtoon/999/1000"))
        assertTrue(adapter.matches("https://sbxh9.com/novel/35110"))
        assertTrue(adapter.matches("https://www.sbxh12.org/novel/1/2"))
        assertFalse("another site", adapter.matches("https://example.com/novel/1"))
        assertFalse("wrong kind", adapter.matches("https://ntk01.com/comic/1"))
        assertFalse("host needs a number", adapter.matches("https://ntk.com/novel/1"))
        assertFalse("not a work path", adapter.matches("https://ntk01.com/"))
    }

    @Test
    fun ownsTheRotatingMirrorHosts() {
        assertTrue(adapter.ownsSite("ntk01.com"))
        assertTrue(adapter.ownsSite("newtoki468.com"))
        assertTrue(adapter.ownsSite("sbxh12.org"))
        assertTrue(adapter.ownsSite("www.newtoki1.com"))
        assertFalse(adapter.ownsSite("example.com"))
        assertFalse(adapter.ownsSite("ntk.com"))
    }

    @Test
    fun aChapterUrlCanonicalisesToItsBookIndex() {
        assertEquals("https://sbxh9.com/novel/35110", adapter.canonicalUrl("https://sbxh9.com/novel/35110/812345"))
        assertEquals("https://newtoki468.com/webtoon/999", adapter.canonicalUrl("https://newtoki468.com/webtoon/999/1000"))
        // A book-index URL is already canonical.
        assertEquals("https://sbxh9.com/novel/35110", adapter.canonicalUrl("https://sbxh9.com/novel/35110"))
        // A URL this adapter does not own is left as-is.
        assertEquals("https://example.com/novel/1", adapter.canonicalUrl("https://example.com/novel/1"))
    }
}
