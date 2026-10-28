package com.shirochi.npia.sources.engines

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SiteIndexTest {
    private val webToEpub = RuleEngine.WEBTOEPUB
    private val rules = RuleEngine.NOVEL_DOWNLOADER

    // WebToEpub: parserFactory.register("host" occurrences, www. ignored on both sides

    @Test
    fun webToEpubHostsAreTheRegisteredOnesWithoutWww() {
        val index = SiteIndex.parse(
            webToEpub,
            """
            parserFactory.register("www.example.org", () => new A());
            parserFactory.register("Novel.Example.COM", () => new B());
            parserFactory.registerRule((url) => 0, () => new C());
            parserFactory.register( "spaced.example", () => new D());
            """.trimIndent(),
        )
        assertEquals(setOf("example.org", "novel.example.com"), index.hosts)
        assertTrue(index.knows(webToEpub, "https://example.org/book/1"))
        assertTrue(index.knows(webToEpub, "https://www.example.org/book/1"))
        assertTrue(index.knows(webToEpub, "http://NOVEL.example.com:8080/x"))
        assertFalse(index.knows(webToEpub, "https://other.example.org/"))
        assertFalse(index.knows(webToEpub, "https://spaced.example/"))
        assertFalse(index.knows(webToEpub, "not a url"))
        assertFalse(index.knows(webToEpub, "https:///path"))
    }

    // novel-downloader: case"host": strings of the switch before "Not Found Rule!", plus host patterns

    private val rulesText = run {
        val backslash = '\\'
        // The bundle spells some hosts with unicode escapes (a backslash, u and four hex digits).
        val escaped = "book.${backslash}u0073ite.com"
        """
        function other(e){switch(e){case"before.example":break}}
        async function getRule(){const e=location.host;let t;
        switch(e){case"101kks.com":{t=3;break}case"www.sudugu.com":case"Alt.Example.com:8080":{t=4;break}case"$escaped":{t=5;break}default:}
        if(/lofter\.com$/.test(e)){t=1}else if(/^(?:www\.)?sbxh\d+\.(?:com|org)$/.test(e)){t=2}
        if(!t)throw new Error("Not Found Rule!")
        }
        function after(e){switch(e){case"after.example":break}}
        """.trimIndent()
    }

    @Test
    fun ruleHostsComeFromTheLastSwitchBeforeTheNotFoundThrow() {
        val index = SiteIndex.parse(rules, rulesText)
        assertEquals(setOf("101kks.com", "www.sudugu.com", "alt.example.com:8080", "book.site.com"), index.hosts)
        assertTrue(index.knows(rules, "https://101kks.com/book/1"))
        assertTrue(index.knows(rules, "https://WWW.Sudugu.com/1001/"))
        assertTrue("\\uXXXX escapes are decoded", index.knows(rules, "https://book.site.com/n/1"))
        assertFalse("outside the region", index.knows(rules, "https://before.example/"))
        assertFalse("outside the region", index.knows(rules, "https://after.example/"))
    }

    @Test
    fun ruleHostsAreComparedWithTheirPortAndWithoutAnyWwwStripping() {
        val index = SiteIndex.parse(rules, rulesText)
        assertFalse("sudugu.com is not www.sudugu.com", index.knows(rules, "https://sudugu.com/1001/"))
        assertFalse("www.101kks.com is not 101kks.com", index.knows(rules, "https://www.101kks.com/"))
        assertTrue(index.knows(rules, "http://alt.example.com:8080/x"))
        assertFalse("the port is part of the host", index.knows(rules, "http://alt.example.com/x"))
    }

    @Test
    fun ruleHostPatternsAreMatchedAgainstTheHost() {
        val index = SiteIndex.parse(rules, rulesText)
        assertTrue(index.knows(rules, "https://www.lofter.com/post"))
        assertTrue(index.knows(rules, "https://someone.lofter.com/post"))
        assertTrue(index.knows(rules, "https://sbxh123.org/novel/1"))
        assertTrue(index.knows(rules, "https://www.sbxh9.com/novel/1"))
        assertFalse(index.knows(rules, "https://lofter.com.evil.example/"))
        assertFalse(index.knows(rules, "https://sbxh.com/"))
    }

    @Test
    fun aBundleWithoutTheRuleLookupListsNothing() {
        val index = SiteIndex.parse(rules, "function nothing(){}")
        assertTrue(index.hosts.isEmpty())
        assertFalse(index.knows(rules, "https://101kks.com/"))
    }

    // The bundles that ship with the app

    @Test
    fun theShippedRulesListTheirSites() {
        val bundle = RuleEngines.store.shipped(rules)!!
        assertTrue(bundle.knows("https://ncode.syosetu.com/n1234ab/"))
        assertTrue(bundle.knows("https://www.sudugu.com/1001/"))
        assertTrue("host pattern", bundle.knows("https://booktoki123.com/novel/1"))
        assertFalse(bundle.knows("https://novelfull.com/book.html"))
        assertFalse(bundle.knows("https://unknown.example/book"))
        assertTrue("hundreds of hosts", bundle.hosts.size > 200)
    }

    @Test
    fun theShippedParsersListTheirSites() {
        val bundle = RuleEngines.store.shipped(webToEpub)!!
        assertTrue(bundle.knows("https://novelfull.com/the-legendary-mechanic.html"))
        assertTrue(bundle.knows("https://www.novelfull.com/the-legendary-mechanic.html"))
        assertFalse(bundle.knows("https://unknown.example/book"))
        assertFalse("not an http URL", bundle.knows("novelfull.com"))
        assertTrue("hundreds of hosts", bundle.hosts.size > 500)
        assertTrue("www. is stripped", bundle.hosts.none { it.startsWith("www.") })
    }
}
