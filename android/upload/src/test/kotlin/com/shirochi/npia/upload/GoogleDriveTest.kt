package com.shirochi.npia.upload

import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.UploadSettings
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.URLDecoder
import java.util.concurrent.atomic.AtomicBoolean

class GoogleDriveTest : UploadTestBase() {
    private val config = GoogleOAuthConfig.of("1234-abcd.apps.googleusercontent.com")!!

    private class StubBrowser(val respond: (String) -> String) : OAuthBrowser {
        override suspend fun authenticate(url: String, callbackScheme: String): String = respond(url)
    }

    @Test
    fun placeholderClientIdIsNotAConfig() {
        assertNull(GoogleOAuthConfig.of(null))
        assertNull(GoogleOAuthConfig.of(""))
        assertNull(GoogleOAuthConfig.of("REPLACE_ME"))
        assertNull(GoogleOAuthConfig.of("\$(NPIA_GOOGLE_CLIENT_ID)"))
        assertNull(GoogleOAuthConfig.of(".apps.googleusercontent.com"))
    }

    @Test
    fun bundledPlaceholderLeavesDriveUnconfigured() {
        GoogleOAuthConfig.clientIdOverride = null
        assertNull(GoogleOAuthConfig.configured())
        try {
            GoogleOAuthConfig.clientIdOverride = config.clientId
            assertEquals(config, GoogleOAuthConfig.configured())
        } finally {
            GoogleOAuthConfig.clientIdOverride = null
        }
    }

    @Test
    fun redirectUsesReversedClientId() {
        assertEquals("com.googleusercontent.apps.1234-abcd", config.callbackScheme)
        assertEquals("com.googleusercontent.apps.1234-abcd:/oauth2redirect", config.redirectUri)
    }

    @Test
    fun pkceMatchesRfc7636Vector() {
        val pkce = Pkce("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        assertEquals("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", pkce.challenge)
        val generated = Pkce.generate().verifier
        assertTrue(generated.length >= 43)
        assertFalse(generated.any { it in "+/=" })
    }

    @Test
    fun authorizationUrl() {
        val pkce = Pkce("v")
        val url = GoogleDriveAuth.authorizationUrl(config, pkce, "s1")
        assertEquals("accounts.google.com", url.host)
        assertEquals(config.clientId, url.queryParameter("client_id"))
        assertEquals(config.redirectUri, url.queryParameter("redirect_uri"))
        assertEquals("code", url.queryParameter("response_type"))
        assertEquals("https://www.googleapis.com/auth/drive.file", url.queryParameter("scope"))
        assertEquals(pkce.challenge, url.queryParameter("code_challenge"))
        assertEquals("S256", url.queryParameter("code_challenge_method"))
        assertEquals("s1", url.queryParameter("state"))
    }

    @Test
    fun callbackParsing() {
        val ok = "com.googleusercontent.apps.1234-abcd:/oauth2redirect?state=s1&code=4/abc&scope=x"
        assertEquals("4/abc", GoogleDriveAuth.code(ok, "s1"))
        assertThrows(UploadException.Rejected::class.java) { GoogleDriveAuth.code(ok, "other") }
        val denied = "com.googleusercontent.apps.1234-abcd:/oauth2redirect?error=access_denied&state=s1"
        assertThrows(CancellationException::class.java) { GoogleDriveAuth.code(denied, "s1") }
    }

    @Test
    fun signInExchangesCodeAndStoresRefreshToken() = runBlocking<Unit> {
        val secrets = InMemorySecretStore()
        handler = { request ->
            if (request.url != GoogleOAuthConfig.TOKEN_ENDPOINT || request.method != "POST") {
                unexpected("unexpected ${request.method} ${request.url}")
            } else {
                json("""{"access_token":"at1","expires_in":3599,"refresh_token":"rt1","token_type":"Bearer"}""")
            }
        }
        val auth = GoogleDriveAuth(config, secrets, client)
        val browser = StubBrowser { url ->
            val state = url.toHttpUrl().queryParameter("state").orEmpty()
            "com.googleusercontent.apps.1234-abcd:/oauth2redirect?code=c0de&state=$state"
        }

        auth.signIn(browser)

        assertEquals("rt1", secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN))
        assertTrue(auth.isSignedIn)
        val fields = seen.first().text.split("&").associate { pair ->
            val (key, value) = pair.split("=", limit = 2)
            key to URLDecoder.decode(value, "UTF-8")
        }
        assertEquals("authorization_code", fields["grant_type"])
        assertEquals("c0de", fields["code"])
        assertEquals(config.redirectUri, fields["redirect_uri"])
        assertEquals(config.clientId, fields["client_id"])
        assertNotNull(fields["code_verifier"])
        assertEquals("cached token is reused without a request", "at1", auth.accessToken())
        assertEquals(1, seen.size)
    }

    @Test
    fun revokedRefreshTokenSignsOut() = runBlocking<Unit> {
        val secrets = InMemorySecretStore(mapOf(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN to "old"))
        handler = { json("""{"error":"invalid_grant"}""", status = 400) }
        val auth = GoogleDriveAuth(config, secrets, client)
        try {
            auth.accessToken()
            fail("expected failure")
        } catch (e: UploadException) {
            assertEquals(UploadException.NotSignedIn("Google Drive"), e)
        }
        assertNull(secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN))
    }

    @Test
    fun signOutRevokesAndForgets() = runBlocking<Unit> {
        val secrets = InMemorySecretStore(mapOf(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN to "rt"))
        handler = { json("{}") }
        GoogleDriveAuth(config, secrets, client).signOut()
        assertEquals(GoogleOAuthConfig.REVOKE_ENDPOINT, seen.single().url)
        assertEquals("token=rt", seen.single().text)
        assertNull(secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN))
    }

    @Test
    fun resumableUploadInChunksThenShares() = runBlocking<Unit> {
        val file = makeFile("Novel.epub", bytes = 600 * 1024)
        val session = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=U1".toHttpUrl()
        val failedOnce = AtomicBoolean(false)
        handler = { request ->
            val url = request.url
            when {
                url == GoogleOAuthConfig.TOKEN_ENDPOINT -> json("""{"access_token":"at","expires_in":3600}""")
                url.encodedPath == "/upload/drive/v3/files" && request.method == "POST" ->
                    json("", headers = mapOf("Location" to session.toString()))
                url == session -> when (val range = request.header("Content-Range").orEmpty()) {
                    "bytes 0-262143/614400" -> json("", status = 308, headers = mapOf("Range" to "bytes=0-262143"))
                    "bytes 262144-524287/614400" ->
                        if (failedOnce.compareAndSet(false, true)) {
                            text("backend error", status = 503)
                        } else {
                            json("", status = 308, headers = mapOf("Range" to "bytes=0-524287"))
                        }
                    "bytes */614400" -> json("", status = 308, headers = mapOf("Range" to "bytes=0-262143"))
                    "bytes 524288-614399/614400" ->
                        json("""{"id":"F1","name":"Novel.epub","webViewLink":"https://drive.google.com/file/d/F1/view?usp=drivesdk"}""")
                    else -> unexpected("unexpected range $range")
                }
                url.encodedPath == "/drive/v3/files/F1/permissions" -> json("""{"id":"anyoneWithLink","type":"anyone","role":"reader"}""")
                else -> unexpected("unexpected request $url")
            }
        }
        val settings = UploadSettings(googleDriveFolderId = "FOLDER")
        val uploader = GoogleDriveUploader(chunkSize = 256 * 1024, retryDelayMillis = { 1 })
        val ctx = context(secrets = mapOf(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN to "rt"), settings = settings, google = config)
        assertTrue(uploader.isReady(ctx))

        val box = ProgressBox()
        val result = uploader.upload(file, ctx, box::add)

        assertEquals("https://drive.google.com/file/d/F1/view?usp=drivesdk", result.link)
        assertNull(result.expiresAt)
        assertEquals(UploadProgress(614_400, 614_400), box.last)

        val start = seen.first { it.url.encodedPath == "/upload/drive/v3/files" && it.method == "POST" }
        assertEquals("resumable", start.url.queryParameter("uploadType"))
        assertEquals("Bearer at", start.header("Authorization"))
        assertEquals("application/epub+zip", start.header("X-Upload-Content-Type"))
        assertEquals("614400", start.header("X-Upload-Content-Length"))
        val metadata = Json.parseToJsonElement(start.text) as JsonObject
        assertEquals(JsonPrimitive("Novel.epub"), metadata["name"])
        assertEquals(JsonArray(listOf(JsonPrimitive("FOLDER"))), metadata["parents"])

        val ranges = seen.filter { it.url == session }.mapNotNull { it.header("Content-Range") }
        assertEquals(
            listOf(
                "bytes 0-262143/614400",
                "bytes 262144-524287/614400",
                "bytes */614400",
                "bytes 262144-524287/614400",
                "bytes 524288-614399/614400",
            ),
            ranges,
        )
        val lastChunk = seen.last { it.url == session }
        assertEquals(614_400 - 524_288, lastChunk.body.size)
        assertEquals(file.readBytes().copyOfRange(524_288, 614_400).toList(), lastChunk.body.toList())

        val share = seen.last()
        assertEquals("/drive/v3/files/F1/permissions", share.url.encodedPath)
        assertEquals(
            Json.parseToJsonElement("""{"role":"reader","type":"anyone"}"""),
            Json.parseToJsonElement(share.text),
        )
    }

    @Test
    fun expiredSessionStopsUpload() = runBlocking<Unit> {
        handler = { request ->
            when {
                request.url == GoogleOAuthConfig.TOKEN_ENDPOINT -> json("""{"access_token":"at","expires_in":3600}""")
                request.method == "POST" -> json("", headers = mapOf("Location" to "https://www.googleapis.com/upload/drive/v3/files?upload_id=X"))
                else -> text("gone", status = 410)
            }
        }
        val ctx = context(secrets = mapOf(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN to "rt"), google = config)
        try {
            GoogleDriveUploader().upload(makeFile(), ctx)
            fail("expected failure")
        } catch (e: UploadException) {
            assertEquals(UploadException.Rejected("Google Drive", "upload session expired"), e)
        }
    }

    @Test
    fun nextOffsetFromRangeHeader() {
        assertEquals(1_048_576L, GoogleDriveUploader.nextOffset(reply("", status = 308, headers = mapOf("Range" to "bytes=0-1048575"))))
        assertEquals(0L, GoogleDriveUploader.nextOffset(reply("", status = 308)))
    }

    @Test
    fun notReadyWithoutClientIdOrToken() {
        assertFalse(GoogleDriveUploader().isReady(context(secrets = mapOf(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN to "rt"))))
        assertFalse(GoogleDriveUploader().isReady(context(google = config)))
    }
}
