package com.shirochi.npia.sources.session

import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.sources.hard.FakeCookies
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GenericCookieSessionTest {
    private val session = GenericCookieSession(SecretKey("test.appCookie"), listOf("https://example.com/"), "example.com")

    @Test
    fun saveWritesTheStoreAndKeepsACopy() {
        val secrets = InMemorySecretStore()
        val cookies = FakeCookies()
        assertTrue(session.save("a=1; b=2", secrets, cookies))
        assertEquals("1", cookies.values["a"])
        assertEquals("2", cookies.values["b"])
        assertTrue(session.hasSession(secrets))
        assertEquals("a=1; b=2", session.savedHeader(secrets))
    }

    @Test
    fun restorePutsSavedCookiesBackAfterTheStoreLosesThem() {
        val secrets = InMemorySecretStore()
        val cookies = FakeCookies()
        session.save("ses=xyz", secrets, cookies)
        cookies.values.clear()
        assertEquals(1, session.restore(secrets, cookies))
        assertEquals("xyz", cookies.values["ses"])
    }

    @Test
    fun signOutRemovesTheCookiesAndTheCopy() {
        val secrets = InMemorySecretStore()
        val cookies = FakeCookies()
        session.save("ses=xyz", secrets, cookies)
        session.signOut(secrets, cookies)
        assertFalse(session.hasSession(secrets))
        assertTrue("ses" in cookies.removed)
    }

    @Test
    fun saveRejectsAHeaderWithNoPairs() {
        val secrets = InMemorySecretStore()
        assertFalse(session.save("garbage", secrets, FakeCookies()))
        assertFalse(session.hasSession(secrets))
    }

    @Test
    fun restoreWithNoSavedCopyDoesNothing() {
        assertEquals(0, session.restore(InMemorySecretStore(), FakeCookies()))
    }
}
