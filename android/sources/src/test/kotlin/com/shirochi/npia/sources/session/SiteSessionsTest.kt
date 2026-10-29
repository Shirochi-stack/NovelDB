package com.shirochi.npia.sources.session

import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.hard.FakeCookies
import com.shirochi.npia.sources.qdn.QdnSite
import com.shirochi.npia.sources.rbooks.RbooksSite
import com.shirochi.npia.sources.sfc.SfcAdapter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class SiteSessionsTest {
    private val qdn = HardSessions.qdn
    private val rbooks = HardSessions.rbooks
    private val copyKey = SecretKey.source(SourceId.QDN, "sessionCookies")

    @Before
    fun freshProcess() = SiteSessions.resetProcessState()

    // What counts as a login

    @Test
    fun qdnNeedsBothLoginCookies() {
        val none = SiteSessions(InMemorySecretStore()) { FakeCookies() }
        assertFalse(none.status(qdn).usable)
        assertTrue(none.status(qdn).present.isEmpty())

        val half = SiteSessions(InMemorySecretStore()) { FakeCookies(mapOf("ywkey" to "k")) }
        assertFalse(half.status(qdn).usable)
        assertEquals(setOf("ywkey"), half.status(qdn).present)

        val both = SiteSessions(InMemorySecretStore()) { FakeCookies(mapOf("ywkey" to "k", "ywguid" to "g", "unrelated" to "x")) }
        assertTrue(both.status(qdn).usable)
        assertEquals(setOf("ywkey", "ywguid"), both.status(qdn).present)
    }

    @Test
    fun emptyCookieValuesAreNotALogin() {
        val sessions = SiteSessions(InMemorySecretStore()) { FakeCookies(mapOf("ywkey" to "", "ywguid" to "")) }
        assertFalse(sessions.status(qdn).usable)
    }

    @Test
    fun rbooksNeedsAnAccessOrRefreshToken() {
        for (name in listOf(RbooksSite.ACCESS_COOKIE, RbooksSite.REFRESH_COOKIE, RbooksSite.GLOBAL_COOKIE)) {
            val sessions = SiteSessions(InMemorySecretStore()) { FakeCookies(mapOf(name to "t")) }
            assertTrue(name, sessions.status(rbooks).usable)
        }
        val other = SiteSessions(InMemorySecretStore()) { FakeCookies(mapOf("ywkey" to "k", "ywguid" to "g")) }
        assertFalse(other.status(rbooks).usable)
    }

    @Test
    fun noCookieStoreMeansNoLogin() {
        val sessions = SiteSessions(InMemorySecretStore()) { null }
        assertFalse(sessions.status(qdn).usable)
        assertEquals(0, sessions.snapshot(qdn))
        assertEquals(0, sessions.restore(qdn))
        sessions.signOut(qdn) // must not throw
    }

    // The saved copy

    @Test
    fun theLoginIsCopiedToTheSecretStoreAndOnlyThoseCookiesAreKept() {
        val secrets = InMemorySecretStore()
        val cookies = FakeCookies(mapOf("ywkey" to "k1", "ywguid" to "g1", "tracking" to "secret-looking"))
        val sessions = SiteSessions(secrets) { cookies }
        assertFalse(sessions.status(qdn).hasSavedCopy)
        assertEquals(2, sessions.snapshot(qdn))
        val copy = secrets.get(copyKey)!!
        assertTrue(copy.contains("k1") && copy.contains("g1"))
        assertFalse(copy.contains("tracking") || copy.contains("secret-looking"))
        assertTrue(sessions.status(qdn).hasSavedCopy)
    }

    @Test
    fun aBrowserThatNeverOpenedTheSiteKeepsTheEarlierCopy() {
        val secrets = InMemorySecretStore()
        SiteSessions(secrets) { FakeCookies(mapOf("ywkey" to "k1", "ywguid" to "g1")) }.snapshot(qdn)
        val before = secrets.get(copyKey)
        assertEquals(0, SiteSessions(secrets) { FakeCookies() }.snapshot(qdn))
        assertEquals(before, secrets.get(copyKey))
    }

    @Test
    fun aLaterLoginReplacesTheCopy() {
        val secrets = InMemorySecretStore()
        SiteSessions(secrets) { FakeCookies(mapOf("ywkey" to "old", "ywguid" to "old")) }.snapshot(qdn)
        SiteSessions(secrets) { FakeCookies(mapOf("ywkey" to "new", "ywguid" to "new")) }.snapshot(qdn)
        assertTrue(secrets.get(copyKey)!!.contains("new"))
        assertFalse(secrets.get(copyKey)!!.contains("old"))
    }

    @Test
    fun theCopiesOfTheTwoSitesAreSeparate() {
        val secrets = InMemorySecretStore()
        val cookies = FakeCookies(mapOf("ywkey" to "k", "ywguid" to "g", RbooksSite.ACCESS_COOKIE to "at"))
        val sessions = SiteSessions(secrets) { cookies }
        sessions.snapshot(qdn)
        sessions.snapshot(rbooks)
        assertNotNull(secrets.get(copyKey))
        assertNotNull(secrets.get(SecretKey.source(SourceId.RBOOKS, "sessionCookies")))
        assertFalse(secrets.get(copyKey)!!.contains("at"))
    }

    // Restore

    @Test
    fun lostSessionCookiesComeBackFromTheCopy() {
        val secrets = InMemorySecretStore()
        SiteSessions(secrets) { FakeCookies(mapOf("ywkey" to "k1", "ywguid" to "g1")) }.snapshot(qdn)
        val restarted = FakeCookies()
        val sessions = SiteSessions(secrets) { restarted }
        assertEquals(2, sessions.restore(qdn))
        assertEquals(mapOf("ywkey" to "k1", "ywguid" to "g1"), restarted.values)
        assertTrue(sessions.status(qdn).usable)
    }

    @Test
    fun restoreNeverOverwritesACookieTheBrowserHas() {
        val secrets = InMemorySecretStore()
        SiteSessions(secrets) { FakeCookies(mapOf("ywkey" to "saved", "ywguid" to "saved")) }.snapshot(qdn)
        val cookies = FakeCookies(mapOf("ywkey" to "newer"))
        assertEquals(1, SiteSessions(secrets) { cookies }.restore(qdn))
        assertEquals("newer", cookies.values["ywkey"])
        assertEquals("saved", cookies.values["ywguid"])
    }

    @Test
    fun restoreOnlyHappensOncePerProcess() {
        val secrets = InMemorySecretStore()
        SiteSessions(secrets) { FakeCookies(mapOf("ywkey" to "k", "ywguid" to "g")) }.snapshot(qdn)
        val cookies = FakeCookies()
        val sessions = SiteSessions(secrets) { cookies }
        assertEquals(2, sessions.restoreOnce(qdn))
        cookies.values.clear() // the user signed out in the browser afterwards
        assertEquals(0, sessions.restoreOnce(qdn))
        assertTrue(cookies.values.isEmpty())
    }

    @Test
    fun aDamagedCopyIsIgnored() {
        val secrets = InMemorySecretStore(mapOf(copyKey to "{not json"))
        val cookies = FakeCookies()
        assertEquals(0, SiteSessions(secrets) { cookies }.restore(qdn))
        assertTrue(cookies.values.isEmpty())

        val odd = InMemorySecretStore(mapOf(copyKey to """{"cookies":{"sneaky":"x","ywkey":"k","ywguid":42,"ywopenid":""}}"""))
        val restored = FakeCookies()
        SiteSessions(odd) { restored }.restore(qdn)
        // Only known login cookie names with real values come back.
        assertEquals(mapOf("ywkey" to "k", "ywguid" to "42"), restored.values)
    }

    // Sign out

    @Test
    fun signOutRemovesTheCookiesOnEveryHostAndTheCopy() {
        val secrets = InMemorySecretStore()
        val cookies = FakeCookies(mapOf("ywkey" to "k", "ywguid" to "g", "other" to "x"))
        val sessions = SiteSessions(secrets) { cookies }
        sessions.snapshot(qdn)
        sessions.signOut(qdn)
        assertEquals(mapOf("other" to "x"), cookies.values)
        assertNull(secrets.get(copyKey))
        assertFalse(sessions.status(qdn).usable)
        // The saved copy is gone, so the next start cannot bring the login back.
        assertEquals(0, sessions.restore(qdn))
    }

    @Test
    fun signingOutOfOneSiteLeavesTheOther() {
        val secrets = InMemorySecretStore()
        val cookies = FakeCookies(mapOf("ywkey" to "k", "ywguid" to "g", RbooksSite.ACCESS_COOKIE to "at"))
        val sessions = SiteSessions(secrets) { cookies }
        sessions.snapshot(qdn)
        sessions.snapshot(rbooks)
        sessions.signOut(qdn)
        assertTrue(sessions.status(rbooks).usable)
        assertNotNull(secrets.get(SecretKey.source(SourceId.RBOOKS, "sessionCookies")))
    }

    @Test
    fun theBrowserTabsSignOutOnlyDropsTheCopy() {
        val secrets = InMemorySecretStore()
        val sessions = SiteSessions(secrets) { FakeCookies(mapOf("ywkey" to "k", "ywguid" to "g")) }
        sessions.snapshot(qdn)
        sessions.forgetSavedCopy(qdn)
        assertNull(secrets.get(copyKey))
    }

    // The sites

    @Test
    fun theSitesAreFoundByTheirHosts() {
        assertEquals(listOf(qdn), HardSessions.forHost("www.${QdnSite.DOMAIN}"))
        assertEquals(listOf(qdn), HardSessions.forHost("m.${QdnSite.DOMAIN}"))
        assertEquals(listOf(qdn), HardSessions.forHost(QdnSite.DOMAIN))
        assertEquals(listOf(rbooks), HardSessions.forHost("account.${RbooksSite.DOMAIN}"))
        assertEquals(emptyList<SessionSite>(), HardSessions.forHost("example.com"))
        assertEquals(qdn, HardSessions.of(SourceId.QDN))
        assertEquals(rbooks, HardSessions.of(SourceId.RBOOKS))
        assertEquals(listOf(HardSessions.sfc), HardSessions.forHost("m.${SfcAdapter.DOMAIN}"))
        assertEquals(HardSessions.sfc, HardSessions.of(SourceId.SFC))
        assertNull(HardSessions.of(SourceId.NPIA))
    }

    @Test
    fun theSfcWebsiteLoginIsItsTicket() {
        val sfc = HardSessions.sfc
        assertEquals(listOf(".SFCommunity"), sfc.cookieNames)
        assertTrue(sfc.signInUrl.endsWith("/login"))
        val signedIn = SiteSessions(InMemorySecretStore()) { FakeCookies(mapOf(".SFCommunity" to "ticket", "other" to "x")) }
        assertTrue(signedIn.status(sfc).usable)
        assertEquals(setOf(".SFCommunity"), signedIn.status(sfc).present)
        assertFalse(SiteSessions(InMemorySecretStore()) { FakeCookies(mapOf("other" to "x")) }.status(sfc).usable)
    }

    @Test
    fun cookieNamesAndSignInPagesAreTheDocumentedOnes() {
        assertEquals(listOf("ywkey", "ywguid", "ywopenid"), qdn.cookieNames)
        assertTrue(qdn.signInUrl.startsWith("https://"))
        assertTrue(RbooksSite.LOGIN_COOKIES.all { it.endsWith("-at") || it.endsWith("-rt") })
        assertTrue(rbooks.signInUrl.endsWith("/account/login"))
    }
}
