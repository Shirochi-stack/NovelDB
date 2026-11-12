package com.shirochi.npia.core

import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SiteOptOutsTest {
    // The shipped list names no site, so the tests bring their own.
    private val site = "optout.example"
    private val listed = setOf(
        MessageDigest.getInstance("SHA-256").digest(site.toByteArray()).joinToString("") { "%02x".format(it) },
    )

    @Test
    fun aSiteAndItsSubdomainsAreOptedOut() {
        listOf(
            "https://$site/novel/some-book/", "http://$site", "https://www.$site/", "https://m.reader.$site/chapter/1?x=1#top",
            "https://${site.uppercase()}/", "https://$site.:443/a", site, "www.$site/novel/1",
            "https://user@$site/a", "https://$site/a book with spaces", "//$site/a",
        ).forEach { assertTrue(it, SiteOptOuts.matches(it, listed)) }
    }

    @Test
    fun otherAddressesAreNot() {
        listOf(
            "", "not a url", "https://example/", "https://other.example/", "https://not$site/",
            "https://$site.evil.test/", "https://evil.test/$site/", "https://evil.test/?u=https://$site/",
            "file:///storage/Library/book.epub",
        ).forEach { assertFalse(it, SiteOptOuts.matches(it, listed)) }
    }

    @Test
    fun theShippedListLeavesOrdinarySitesAlone() {
        assertFalse(SiteOptOuts.matches("https://$site/"))
        assertFalse(SiteOptOuts.matches("https://example.com/novel/1"))
    }

    @Test
    fun theErrorSaysWhy() {
        assertEquals(SiteOptOuts.MESSAGE, SourceException.SiteDisabled("https://$site/").message)
        assertTrue(SiteOptOuts.MESSAGE.contains("request of the site owner"))
    }
}
