package com.shirochi.npia.webengine

import org.junit.Assert.assertEquals
import org.junit.Test

class VisibleBrowserTest {
    @Test
    fun normalizeKeepsUrlsAddsHttpsAndSearchesText() {
        assertEquals("https://example.com/a", VisibleBrowser.normalize(" https://example.com/a "))
        assertEquals("https://example.com/login", VisibleBrowser.normalize("example.com/login"))
        assertEquals("https://duckduckgo.com/?q=light+novel", VisibleBrowser.normalize("light novel"))
        assertEquals("", VisibleBrowser.normalize("  "))
    }

    @Test
    fun registrableDomainHandlesCountrySecondLevels() {
        assertEquals("example.com", SiteCookies.registrableDomain("www.example.com"))
        assertEquals("example.co.kr", SiteCookies.registrableDomain("m.example.co.kr"))
        assertEquals("example.co.jp", SiteCookies.registrableDomain("a.b.example.co.jp"))
        assertEquals("example.com", SiteCookies.registrableDomain("example.com"))
    }
}
