package com.shirochi.npia.sources.tapas

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.sources.hard.FakeCookies
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TapasAppApiTest {
    @Test
    fun loggedInDetectsTheRedirectAwayFromSignIn() {
        assertTrue(TapasAppApi.loggedIn("https://host/", "<html>home</html>"))
        assertFalse(TapasAppApi.loggedIn("https://host/account/signin", "x"))
        assertFalse(TapasAppApi.loggedIn("https://host/account/authenticate", "x"))
        // A failed login re-renders the sign-in form (which posts to /account/authenticate).
        assertFalse(TapasAppApi.loggedIn("https://host/", "<form action=\"/account/authenticate\">"))
    }

    @Test
    fun loginKeepsTheJarCookiesOnSuccess() = runTest {
        val cookies = FakeCookies(mapOf("_ses" to "abc"))
        val http = object : HttpFetching {
            override suspend fun send(request: HttpRequest): HttpResponse =
                if ("authenticate" in request.url) {
                    HttpResponse("https://host/", 200, emptyMap(), "<html>home</html>".encodeToByteArray())
                } else {
                    HttpResponse(request.url, 200, emptyMap(), ByteArray(0))
                }
        }
        val secrets = InMemorySecretStore()
        assertTrue(TapasAppApi.login("user@example.com", "pw", http, secrets, cookies))
        assertEquals("_ses=abc", secrets.get(TapasAppApi.cookieKey))
    }

    @Test
    fun loginFailsWhenStillOnTheSignInPage() = runTest {
        val http = object : HttpFetching {
            override suspend fun send(request: HttpRequest): HttpResponse =
                HttpResponse("https://host/account/signin", 200, emptyMap(), "<form action=\"/account/authenticate\">".encodeToByteArray())
        }
        val secrets = InMemorySecretStore()
        assertFalse(TapasAppApi.login("user", "pw", http, secrets, FakeCookies()))
        assertNull(secrets.get(TapasAppApi.cookieKey))
    }

    @Test
    fun loginRejectsEmptyCredentials() = runTest {
        val http = object : HttpFetching {
            override suspend fun send(request: HttpRequest): HttpResponse = error("should not be called")
        }
        assertFalse(TapasAppApi.login("", "", http, InMemorySecretStore(), FakeCookies()))
    }
}
