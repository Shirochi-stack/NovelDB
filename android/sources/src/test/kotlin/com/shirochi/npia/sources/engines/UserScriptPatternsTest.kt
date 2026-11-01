package com.shirochi.npia.sources.engines

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UserScriptPatternsTest {
    private fun patterns(vararg lines: String) =
        UserScriptPatterns.parse("// ==UserScript==\n" + lines.joinToString("\n") { "// $it" } + "\n// ==/UserScript==\nbody();")

    @Test
    fun starSchemeMeansHttpOrHttpsOnly() {
        val rules = patterns("@match *://site.test/book/*")
        assertTrue(rules.accepts("https://site.test/book/1"))
        assertTrue(rules.accepts("http://site.test/book/1"))
        assertFalse(rules.accepts("ftp://site.test/book/1"))
        assertFalse(rules.accepts("file://site.test/book/1"))
        val onlyHttps = patterns("@match https://site.test/*")
        assertTrue(onlyHttps.accepts("https://site.test/x"))
        assertFalse(onlyHttps.accepts("http://site.test/x"))
    }

    @Test
    fun wildcardsInHostAndPath() {
        val rules = patterns("@match *://*.site.test/info/*", "@match *://other.test/a/*/c")
        // "*." is the site and every subdomain.
        assertTrue(rules.accepts("https://site.test/info/1"))
        assertTrue(rules.accepts("https://book.site.test/info/1"))
        assertTrue(rules.accepts("https://a.b.site.test/info/1"))
        assertFalse(rules.accepts("https://notsite.test/info/1"))
        assertFalse(rules.accepts("https://site.test.evil/info/1"))
        assertFalse(rules.accepts("https://site.test/rank/"))
        // "*" in the path crosses slashes; the pattern must still fit the whole path.
        assertTrue(rules.accepts("https://other.test/a/b/c"))
        assertTrue(rules.accepts("https://other.test/a/b/x/c"))
        assertFalse(rules.accepts("https://other.test/a/b/c/d"))
    }

    @Test
    fun queryCountsAndFragmentAndPortDoNot() {
        val rules = patterns("@match *://site.test/read.php?id=*", "@match *://home.test/")
        assertTrue(rules.accepts("https://site.test/read.php?id=5&x=1"))
        assertFalse(rules.accepts("https://site.test/read.php"))
        assertTrue(rules.accepts("https://site.test:8443/read.php?id=5#top"))
        assertTrue(rules.accepts("https://home.test"))
        assertTrue(rules.accepts("https://home.test/#section"))
        assertFalse(rules.accepts("https://home.test/rank/"))
    }

    @Test
    fun excludeBeatsMatch() {
        val rules = patterns("@match *://site.test/*", "@exclude *://site.test/chapter/*", "@exclude *://site.test/")
        assertTrue(rules.accepts("https://site.test/book/9"))
        assertFalse(rules.accepts("https://site.test/chapter/9"))
        assertFalse(rules.accepts("https://site.test/"))
        assertEquals(1, rules.allowCount)
        assertEquals(2, rules.denyCount)
    }

    @Test
    fun noMatchMeansNoPage() {
        assertFalse(patterns("@exclude *://site.test/*").accepts("https://site.test/a"))
        assertFalse(patterns("@match *://site.test/*").accepts("not a url"))
        assertFalse(patterns("@match *://site.test/*").accepts("mailto:a@site.test"))
    }

    @Test
    fun escapedHostsAreDecoded() {
        val backslash = 92.toChar()
        val rules = patterns("@match *://book.${backslash}u0073${backslash}u0069te.test/info/*", "@exclude *://book.site.test/info/0")
        assertTrue(rules.accepts("https://book.site.test/info/1"))
        assertFalse(rules.accepts("https://book.site.test/info/0"))
        assertEquals("site", UserScriptPatterns.decode("${backslash}u0073${backslash}u0069te"))
        // An incomplete or invalid escape is left as written.
        assertEquals("${backslash}u12", UserScriptPatterns.decode("${backslash}u12"))
        assertEquals("a${backslash}uzzzzb", UserScriptPatterns.decode("a${backslash}uzzzzb"))
    }

    @Test
    fun includeAcceptsRegularExpressionsAndGlobs() {
        val rules = patterns("@include /^https?:\\/\\/(?:www\\.)?toon\\d+\\.com\\/novel\\//", "@include *://glob.test/n/*")
        assertTrue(rules.accepts("https://www.toon12.com/novel/3"))
        assertTrue(rules.accepts("http://toon7.com/novel/3"))
        assertFalse(rules.accepts("https://toon7.com/webtoon/3"))
        assertTrue(rules.accepts("https://glob.test/n/1"))
        assertEquals(2, rules.allowCount)
    }

    @Test
    fun unusableLinesAreSkipped() {
        val rules = patterns("@match not-a-pattern", "@match *://site.test/ok", "@name something", "@include /unterminated(")
        assertEquals(1, rules.allowCount)
        assertNull(UserScriptPatterns.compile("no scheme here"))
    }

    @Test
    fun theHeaderEndsAtItsClosingLine() {
        val rules = UserScriptPatterns.parse(
            "// ==UserScript==\n// @match *://a.test/*\n// ==/UserScript==\n// @match *://b.test/*\n",
        )
        assertTrue(rules.accepts("https://a.test/x"))
        assertFalse(rules.accepts("https://b.test/x"))
    }

    @Test
    fun theShippedRulesBundleHasHundredsOfPagePatterns() {
        val rules = RuleEngines.store.current(RuleEngine.NOVEL_DOWNLOADER)!!
        val patterns = UserScriptPatterns.parse(rules.read())
        assertTrue("match lines: ${patterns.allowCount}", patterns.allowCount > 200)
        assertTrue("exclude lines: ${patterns.denyCount}", patterns.denyCount > 50)
    }
}
