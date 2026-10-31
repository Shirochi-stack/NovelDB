package com.shirochi.npia.sources

import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.core.SourceId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SourceCatalogPagesTest {
    private val defaults = EngineSettings()
    private fun off(vararg engines: SiteEngine) = EngineSettings(disabled = engines.map { it.raw })

    // Source domains are kept as escapes so the names stay out of the repository text.
    private val npia = "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    private val qdn = "\u0071\u0069\u0064\u0069\u0061\u006e.com"

    @Test
    fun builtInScrapersListTheirSites() {
        val withSites = SourceCatalog.adapters.filter { it.sites.isNotEmpty() }.map { it.id }.toSet()
        // ntk and maru are recognised by a name pattern (ownsSite), not a fixed `sites` list, so they
        // list no sites here; `reader` takes any page the script engines list.
        val unported = setOf(SourceId.NTK, SourceId.READER, SourceId.MARU)
        assertEquals(SourceId.entries.toSet() - unported, withSites)
        SourceCatalog.adapters.flatMap { it.sites }.forEach { host ->
            assertEquals(host.lowercase(), host)
            assertFalse("$host has a scheme or path", host.contains("/"))
        }
        assertTrue(SourceCatalog.adapterFor(SourceId.MARU).ownsSite("marumaru123.com"))
        assertFalse(SourceCatalog.adapterFor(SourceId.MARU).ownsSite("example.com"))
        assertTrue(SourceCatalog.adapterFor(SourceId.NTK).ownsSite("newtoki468.com"))
        assertTrue(SourceCatalog.adapterFor(SourceId.NTK).ownsSite("sbxh12.org"))
        assertFalse(SourceCatalog.adapterFor(SourceId.NTK).ownsSite("example.com"))
    }

    @Test
    fun builtInBookPagesAreDownloadable() {
        assertTrue(SourceCatalog.downloadablePage("https://global.$npia/novel/123", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://$npia/novel/4567", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://fenrirealm.com/series/some-title", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://yeduji.com/book/12", defaults))
    }

    @Test
    fun globalNpiaNovelPageCountsButItsHomePageDoesNot() {
        val host = "global.$npia"
        assertTrue(SourceCatalog.supportedSite("https://$host/", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://$host/novel/987", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://$host/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://$host", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://$host/?lang=en", defaults))
    }

    @Test
    fun rulesBookPagesCountThroughTheHeader() {
        assertTrue(SourceCatalog.supportedSite("https://www.$qdn/", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://book.$qdn/info/1035420986/", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://www.$qdn/book/1035420986/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://www.$qdn/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://www.$qdn/rank/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://www.$qdn/all/", defaults))
    }

    @Test
    fun webToEpubOnlySiteCountsOnEveryPageButTheHomePage() {
        val site = "moonbunnycafe.com"
        assertTrue(SourceCatalog.supportedSite("https://$site/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://$site/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://$site", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://$site/?s=query", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://$site/some-novel/", defaults))
        assertTrue(SourceCatalog.downloadablePage("https://www.$site/some-novel/chapter-1", defaults))
    }

    @Test
    fun unknownSitesAreNeitherSupportedNorDownloadable() {
        assertFalse(SourceCatalog.supportedSite("https://example.com/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://example.com/book/1", defaults))
        assertFalse(SourceCatalog.downloadablePage("not a url", defaults))
        assertFalse(SourceCatalog.downloadablePage("ftp://moonbunnycafe.com/a/b", defaults))
    }

    @Test
    fun switchedOffEnginesDoNotCount() {
        val book = "https://book.$qdn/info/1035420986/"
        assertTrue(SourceCatalog.downloadablePage(book, defaults))
        assertFalse(SourceCatalog.downloadablePage(book, off(SiteEngine.NOVEL_DOWNLOADER)))
        assertTrue(SourceCatalog.downloadablePage(book, off(SiteEngine.NATIVE, SiteEngine.WEBTOEPUB)))
        // qdn's own scraper owns the site, so it stays supported (its links are never renamed) even
        // with the built-in scrapers off, but then only a switched-on engine can offer its pages.
        assertTrue(SourceCatalog.supportedSite("https://www.$qdn/", off(SiteEngine.NATIVE, SiteEngine.NOVEL_DOWNLOADER)))
        assertTrue(SourceCatalog.downloadablePage("https://m.$qdn/book/1035420986/", defaults))
        assertFalse(SourceCatalog.downloadablePage("https://m.$qdn/book/1035420986/", off(SiteEngine.NATIVE, SiteEngine.NOVEL_DOWNLOADER)))

        val toon = "https://moonbunnycafe.com/some-novel/"
        assertFalse(SourceCatalog.downloadablePage(toon, off(SiteEngine.WEBTOEPUB)))
        assertTrue(SourceCatalog.downloadablePage(toon, off(SiteEngine.NATIVE, SiteEngine.NOVEL_DOWNLOADER)))
    }

    @Test
    fun withTheBuiltInScrapersOffABuiltInPageNeedsAnEngineToListIt() {
        val novel = "https://global.$npia/novel/987"
        // WebToEpub lists this host (any page but the home page); the rules do not.
        assertTrue(SourceCatalog.downloadablePage(novel, off(SiteEngine.NATIVE)))
        assertFalse(SourceCatalog.downloadablePage(novel, off(SiteEngine.NATIVE, SiteEngine.WEBTOEPUB)))
        assertTrue(SourceCatalog.downloadablePage(novel, off(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB)))
    }

    @Test
    fun theAddScreenStillTakesAnyWebLink() {
        assertTrue(SourceCatalog.resolve("https://example.com/anything", defaults) != null)
    }
}
