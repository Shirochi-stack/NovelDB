package com.shirochi.npia.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowserTranslateTest {
    @Test
    fun theLanguageSettingFallsBackToEnglish() {
        assertEquals("en", BrowserTranslate.DEFAULT_LANGUAGE)
        assertEquals("English", BrowserTranslate.language(null).name)
        assertEquals("English", BrowserTranslate.language("").name)
        assertEquals("English", BrowserTranslate.language("xx").name)
        assertEquals("ko", BrowserTranslate.language("ko").code)
        assertEquals("zh-CN", BrowserTranslate.language("zh-cn").code)
        assertTrue(BrowserTranslate.languages.map { it.code }.containsAll(listOf("en", "ko", "ja", "zh-CN", "es")))
        assertEquals(BrowserTranslate.languages.size, BrowserTranslate.languages.map { it.code }.toSet().size)
    }

    @Test
    fun theTranslatedCopyAddressNamesTheLanguageAndThePage() {
        assertEquals(
            "https://translate.google.com/translate?sl=auto&tl=zh-CN&u=https%3A%2F%2Fexample.com%2Fnovel%2F1%3Fa%3Db",
            BrowserTranslate.copyUrl("https://example.com/novel/1?a=b", "zh-CN"),
        )
    }

    @Test
    fun theProxyAddressMapsBackToTheOriginalSite() {
        val back = BrowserTranslate::originalUrl
        assertEquals("https://global.example.com/novel/123", back("https://global-example-com.translate.goog/novel/123"))
        assertEquals(
            "https://global.example.com/novel/123?page=2",
            back("https://global-example-com.translate.goog/novel/123?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en&page=2&_x_tr_pto=wapp"),
        )
        assertEquals("https://global.example.com/novel/123", back("https://global-example-com.translate.goog/novel/123?_x_tr_sl=ko&_x_tr_tl=en"))
        assertEquals("https://global.example.com/", back("https://global-example-com.translate.goog"))
        // A dash in the original host is doubled.
        assertEquals("https://my-site.example.com/a", back("https://my--site-example-com.translate.goog/a?_x_tr_sl=auto"))
        assertEquals("https://example.com/a#part", back("https://example-com.translate.goog/a?_x_tr_sl=auto#part"))
        // Google's own address carries the page in u.
        assertEquals(
            "https://example.com/novel/1?a=b",
            back("https://translate.google.com/translate?sl=auto&tl=en&u=https%3A%2F%2Fexample.com%2Fnovel%2F1%3Fa%3Db"),
        )
    }

    @Test
    fun otherAddressesAreNotTranslatedCopies() {
        assertNull(BrowserTranslate.originalUrl("https://example.com/novel/1"))
        assertNull(BrowserTranslate.originalUrl("https://translate.google.com/"))
        assertNull(BrowserTranslate.originalUrl("https://translate.google.com/translate?u=javascript:alert(1)"))
        assertNull(BrowserTranslate.originalUrl("https://evil.translate.goog.example.com/a"))
        assertNull(BrowserTranslate.originalUrl("https://a.b.translate.goog/a"))
        assertNull(BrowserTranslate.originalUrl("not a url"))
        assertFalse(BrowserTranslate.isTranslatedCopy("https://example.com/"))
        assertTrue(BrowserTranslate.isTranslatedCopy("https://example-com.translate.goog/"))
    }

    @Test
    fun copyAndOriginalRoundTrip() {
        // Google redirects the translate address to the proxy; the original comes back from either form.
        val page = "https://global.example.com/novel/77"
        assertEquals(page, BrowserTranslate.originalUrl(BrowserTranslate.copyUrl(page, "ko")))
    }
}
