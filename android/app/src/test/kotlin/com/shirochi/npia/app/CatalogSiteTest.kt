package com.shirochi.npia.app

import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.SiteEngine
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CatalogSiteTest {
    @Test
    fun acceptsOnlyFullHttpAddresses() {
        assertEquals("https://example.com/books", validCatalogUrl(" https://example.com/books "))
        assertEquals("http://192.168.1.5:8000/", validCatalogUrl("http://192.168.1.5:8000/"))
        assertNull(validCatalogUrl("https://"))
        assertNull(validCatalogUrl("https:// example.com"))
        assertNull(validCatalogUrl("example.com"))
        assertNull(validCatalogUrl("file:///sdcard/index.html"))
        assertNull(validCatalogUrl(null))
    }

    // The classes of page the Catalog tab tells apart. Source domains are kept as escapes.

    private val npia = "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    private val qdn = "\u0071\u0069\u0064\u0069\u0061\u006e.com"
    private val engines = EngineSettings()

    @Test
    fun noveldbAndNoveldbNextAreOneClass() {
        assertTrue(isNovelDbSite(DEFAULT_CATALOG_URL))
        assertTrue(isNovelDbSite("https://shirochi-stack.github.io/NovelDB/?tag=x"))
        assertTrue(isNovelDbSite("https://shirochi-stack.github.io/NovelDB"))
        assertTrue(isNovelDbSite(NOVELDB_NEXT_URL))
        assertTrue(isNovelDbSite("https://noveldb.pages.dev/some/route"))
        assertTrue(isNovelDbSite("https://abc123.noveldb.pages.dev/"))
        // Other pages of the same host are not NovelDB.
        assertFalse(isNovelDbSite("https://shirochi-stack.github.io/other-project/"))
        assertFalse(isNovelDbSite("https://shirochi-stack.github.io/NovelDBX/"))
        assertFalse(isNovelDbSite("https://evil.example/noveldb.pages.dev/"))
        assertFalse(isNovelDbSite("https://notnoveldb.pages.dev/"))
        assertFalse(isNovelDbSite("not a url"))
        for (url in listOf(DEFAULT_CATALOG_URL, NOVELDB_NEXT_URL, "https://noveldb.pages.dev/x")) {
            val mode = catalogPageMode(url, engines, cartLinksSetting = true)
            assertEquals(CatalogKind.NOVELDB, mode.kind)
            assertFalse("links are not renamed on $url", mode.cartLinks)
            assertFalse("no Download button on $url", mode.download)
        }
    }

    @Test
    fun globalNpiaHomeHasNoButtonButANovelPageHasOne() {
        val home = catalogPageMode("https://global.$npia/", engines, cartLinksSetting = true)
        assertEquals(CatalogKind.SUPPORTED, home.kind)
        assertFalse(home.cartLinks)
        assertFalse(home.download)

        val novel = catalogPageMode("https://global.$npia/novel/4321", engines, cartLinksSetting = true)
        assertEquals(CatalogKind.SUPPORTED, novel.kind)
        assertFalse(novel.cartLinks)
        assertTrue(novel.download)
    }

    @Test
    fun engineListedSitesAreSupportedPageByPage() {
        // Rules header: book pages only. The home page and rankings are not downloadable.
        assertTrue(catalogPageMode("https://book.$qdn/info/1035420986/", engines, true).download)
        assertTrue(catalogPageMode("https://www.$qdn/book/1035420986/", engines, true).download)
        assertEquals(CatalogKind.SUPPORTED, catalogKind("https://www.$qdn/rank/", engines))
        assertFalse(catalogPageMode("https://www.$qdn/rank/", engines, true).download)
        assertFalse(catalogPageMode("https://www.$qdn/", engines, true).download)
        // The rules switched off: nobody lists the site any more.
        val noRules = EngineSettings(disabled = listOf(SiteEngine.NOVEL_DOWNLOADER.raw, SiteEngine.WEBTOEPUB.raw))
        assertEquals(CatalogKind.OTHER, catalogKind("https://www.$qdn/", noRules))
    }

    @Test
    fun unknownSitesKeepTodaysBehaviour() {
        val on = catalogPageMode("https://example.com/books/1", engines, cartLinksSetting = true)
        assertEquals(CatalogKind.OTHER, on.kind)
        assertTrue(on.cartLinks)
        assertFalse(on.download)
        assertFalse(catalogPageMode("https://example.com/", engines, cartLinksSetting = false).cartLinks)
    }

    @Test
    fun addressesAreComparedLikeTheBridgeDoes() {
        assertTrue(sameAddress("https://a.test/x/", "https://a.test/x"))
        assertTrue(sameAddress("https://a.test/x?y=1#", "https://a.test/x?y=1"))
        assertTrue(sameAddress("https://a.test/", "https://a.test"))
        assertFalse(sameAddress("https://a.test/x", "https://a.test/y"))
        assertFalse(sameAddress("https://a.test/x#a", "https://a.test/x#b"))
    }

    @Test
    fun titlesLoseTheSiteName() {
        assertEquals("Solo Leveling", cleanPageTitle("Solo Leveling - \u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061", "https://global.$npia/novel/1"))
        assertEquals("Solo Leveling", cleanPageTitle("  Solo   Leveling | \u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061 Global ", "https://global.$npia/novel/1"))
        assertEquals("Hello World", cleanPageTitle("Home - Hello World", "https://www.example.com/a"))
        assertEquals("\u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061", cleanPageTitle("\u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061", "https://global.$npia/"))
        assertEquals("A - B", cleanPageTitle("A - B", "https://www.example.com/a"))
        assertEquals("", cleanPageTitle("  ", "https://example.com"))
        assertEquals(200, cleanPageTitle("x".repeat(500), "https://example.com").length)
        assertEquals("example", siteLabel("https://www.shop.example.co.uk/"))
        assertEquals("example", siteLabel("https://example.com"))
    }
}
