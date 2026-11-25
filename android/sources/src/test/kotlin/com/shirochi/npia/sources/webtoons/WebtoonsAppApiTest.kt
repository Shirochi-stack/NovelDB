package com.shirochi.npia.sources.webtoons

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.sources.hard.FakeCookies
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.KeyPairGenerator
import java.security.interfaces.RSAPublicKey
import javax.crypto.Cipher

class WebtoonsAppApiTest {
    private fun keyPair() = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()

    @Test
    fun rsaEncryptHexRoundTripsWithThePrivateKey() {
        val kp = keyPair()
        val pub = kp.public as RSAPublicKey
        val message = "hello, webtoons".encodeToByteArray()
        // The endpoint labels the exponent "nvalue" and the modulus "evalue"; login picks the larger as
        // the modulus, so pass modulus here directly.
        val hex = WebtoonsAppApi.rsaEncryptHex(message, pub.modulus, pub.publicExponent)
        val cipherBytes = hex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        val cipher = Cipher.getInstance("RSA/ECB/PKCS1Padding").apply { init(Cipher.DECRYPT_MODE, kp.private) }
        assertArrayEquals(message, cipher.doFinal(cipherBytes))
    }

    @Test
    fun signAppendsMsgpadAndMdAndIsDeterministic() {
        val time = "1700000000000".encodeToByteArray()
        val noQuery = WebtoonsAppApi.sign("https://host/path", time)
        assertTrue(noQuery.contains("?msgpad="))
        assertTrue(noQuery.contains("&md="))
        val withQuery = WebtoonsAppApi.sign("https://host/path?v=1", time)
        assertTrue(withQuery.contains("&msgpad="))
        assertEquals(noQuery, WebtoonsAppApi.sign("https://host/path", time))
    }

    @Test
    fun loginStoresTheSessionCookieOnSuccess() = runTest {
        val pub = keyPair().public as RSAPublicKey
        val http = scripted(pub, loginResult = """{"ses":"SESVALUE"}""")
        val cookies = FakeCookies()
        val secrets = InMemorySecretStore()
        assertTrue(WebtoonsAppApi.login("a@b.com", "pw", http, secrets, cookies))
        assertEquals("NEO_SES=SESVALUE", secrets.get(WebtoonsAppApi.cookieKey))
        assertEquals("SESVALUE", cookies.values["NEO_SES"])
    }

    @Test
    fun loginFailsAndSavesNothingWhenNoSessionComesBack() = runTest {
        val pub = keyPair().public as RSAPublicKey
        val http = scripted(pub, loginResult = """{"login_status":110}""")
        val secrets = InMemorySecretStore()
        assertFalse(WebtoonsAppApi.login("a@b.com", "pw", http, secrets, FakeCookies()))
        assertNull(secrets.get(WebtoonsAppApi.cookieKey))
    }

    @Test
    fun loginRejectsEmptyCredentials() = runTest {
        val http = object : HttpFetching {
            override suspend fun send(request: HttpRequest): HttpResponse = error("should not be called")
        }
        assertFalse(WebtoonsAppApi.login("", "", http, InMemorySecretStore(), FakeCookies()))
    }

    /** Serves the server clock, an RSA key built from `pub`, and a scripted loginById result. */
    private fun scripted(pub: RSAPublicKey, loginResult: String) = object : HttpFetching {
        override suspend fun send(request: HttpRequest): HttpResponse {
            val url = request.url
            val body = when {
                "getRsaKey.json" in url ->
                    """{"message":{"result":{"sessionKey":"SK","keyName":"KN","nvalue":"${pub.publicExponent.toString(16)}","evalue":"${pub.modulus.toString(16)}"}}}"""
                "loginById.json" in url -> """{"message":{"result":$loginResult}}"""
                else -> "1700000000000" // currentTime
            }
            return HttpResponse(url, 200, emptyMap(), body.encodeToByteArray())
        }
    }
}
