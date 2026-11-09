package com.shirochi.npia.app

import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.upload.UploadException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import com.shirochi.npia.core.UploadSettings
import com.shirochi.npia.upload.GoogleDriveUploader
import com.shirochi.npia.upload.UploadContext
import org.junit.Assert.*
import org.junit.Test
import java.net.URLDecoder
import java.nio.file.Files
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class GoogleDriveAccountTest {
    private val credentials = GoogleDriveCredentials("1234-user.apps.googleusercontent.com", "user-secret", "user-refresh")
    private val key = SecretKey("upload.gdrive.credentials")

    private fun client(handler: (Request) -> Pair<Int, String>) = OkHttpClient.Builder().addInterceptor { chain ->
        val (status, body) = handler(chain.request())
        Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(status).message("test")
            .body(body.toResponseBody("application/json".toMediaType())).build()
    }.build()

    private fun fields(request: Request): Map<String, String> {
        val buffer = okio.Buffer()
        request.body!!.writeTo(buffer)
        return buffer.readUtf8().split('&').associate { pair ->
            URLDecoder.decode(pair.substringBefore('='), "UTF-8") to URLDecoder.decode(pair.substringAfter('='), "UTF-8")
        }
    }

    @Test
    fun suppliedCredentialsAreValidatedSavedAndUsedAfterRestart() = runBlocking {
        val secrets = InMemorySecretStore()
        val requests = mutableListOf<Map<String, String>>()
        val client = client { request ->
            assertEquals("https://oauth2.googleapis.com/token", request.url.toString())
            requests += fields(request)
            200 to """{"access_token":"access-${requests.size}","expires_in":3600,"refresh_token":"rotated"}"""
        }
        val account = GoogleDriveAccount(secrets, client)
        account.connect(credentials)
        assertTrue(account.signedIn.value)
        assertEquals("access-1", account.accessToken(false))
        assertEquals(1, requests.size)
        assertEquals(mapOf("client_id" to credentials.clientId, "client_secret" to credentials.clientSecret,
            "grant_type" to "refresh_token", "refresh_token" to credentials.refreshToken), requests[0])
        assertEquals("rotated", account.credentials.value.refreshToken)
        assertTrue(secrets.get(key)!!.contains("rotated"))
        assertFalse(credentials.toString().contains("user-secret"))
        assertFalse(credentials.toString().contains("user-refresh"))

        val restored = GoogleDriveAccount(secrets, client)
        assertTrue(restored.signedIn.value)
        assertEquals("access-2", restored.accessToken(false))
        assertEquals("rotated", requests[1]["refresh_token"])
        assertEquals("access-3", restored.accessToken(true))
        restored.disconnect()
        assertFalse(restored.signedIn.value)
        assertNull(secrets.get(key))
        assertEquals(GoogleDriveCredentials(), restored.credentials.value)
    }

    @Test
    fun incompleteCredentialsMakeNoNetworkRequest() = runBlocking {
        val account = GoogleDriveAccount(InMemorySecretStore(), client { error("No request for invalid credentials") })
        for (value in listOf(credentials.copy(clientId = ""), credentials.copy(clientId = "placeholder"), credentials.copy(refreshToken = ""))) {
            assertTrue(runCatching { account.connect(value) }.exceptionOrNull() is IllegalArgumentException)
            assertFalse(account.signedIn.value)
            assertFalse(account.connecting.value)
            assertNotNull(account.signInError.value)
        }
    }

    @Test
    fun rejectedReplacementPreservesTheWorkingGrant() = runBlocking {
        val secrets = InMemorySecretStore()
        val account = GoogleDriveAccount(secrets, client { request ->
            if (fields(request)["refresh_token"] == "bad") 400 to """{"error":"invalid_grant"}"""
            else 200 to """{"access_token":"working","expires_in":3600}"""
        })
        account.connect(credentials)
        val saved = secrets.get(key)
        assertTrue(runCatching { account.connect(credentials.copy(refreshToken = "bad")) }.isFailure)
        assertEquals(saved, secrets.get(key))
        assertEquals("working", account.accessToken(false))
        assertTrue(account.signedIn.value)
        assertNotNull(account.signInError.value)
    }

    @Test
    fun revokedTokenDisconnectsTheStoredGrant() = runBlocking {
        val calls = AtomicInteger()
        val secrets = InMemorySecretStore()
        val account = GoogleDriveAccount(secrets, client {
            if (calls.incrementAndGet() == 1) 200 to """{"access_token":"first","expires_in":3600}"""
            else 400 to """{"error":"invalid_grant"}"""
        })
        account.connect(credentials)
        assertTrue(runCatching { account.accessToken(true) }.exceptionOrNull() is UploadException.NotSignedIn)
        assertFalse(account.signedIn.value)
        assertNull(secrets.get(key))
        assertTrue(account.signInError.value!!.contains("invalid or expired"))
    }

    @Test
    fun disconnectDuringConnectionOrRefreshDoesNotRestoreTokens() = runBlocking {
        for (refresh in listOf(false, true)) {
            val entered = CompletableDeferred<Unit>()
            val release = CountDownLatch(1)
            val calls = AtomicInteger()
            val secrets = InMemorySecretStore()
            val account = GoogleDriveAccount(secrets, client {
                if (calls.incrementAndGet() == if (refresh) 2 else 1) {
                    entered.complete(Unit)
                    check(release.await(10, TimeUnit.SECONDS))
                }
                200 to """{"access_token":"late","expires_in":3600}"""
            })
            if (refresh) account.connect(credentials)
            val pending = async(Dispatchers.Default) {
                runCatching { if (refresh) account.accessToken(true) else account.connect(credentials) }
            }
            try {
                withTimeout(10_000) { entered.await() }
                account.disconnect()
            } finally { release.countDown() }
            assertTrue(pending.await().isFailure)
            assertFalse(account.signedIn.value)
            assertNull(secrets.get(key))
        }
    }

    @Test
    fun emptyAccessTokenIsRejected() = runBlocking {
        val secrets = InMemorySecretStore()
        val account = GoogleDriveAccount(secrets, client { 200 to """{"access_token":"","expires_in":3600}""" })
        assertTrue(runCatching { account.connect(credentials) }.isFailure)
        assertFalse(account.signedIn.value)
        assertNull(secrets.get(key))
    }

    @Test
    fun disconnectAlsoCancelsAQueuedConnection() = runBlocking {
        val entered = CompletableDeferred<Unit>()
        val release = CountDownLatch(1)
        val calls = AtomicInteger()
        val secrets = InMemorySecretStore()
        val account = GoogleDriveAccount(secrets, client {
            calls.incrementAndGet()
            entered.complete(Unit)
            check(release.await(10, TimeUnit.SECONDS))
            200 to """{"access_token":"late","expires_in":3600}"""
        })
        val first = async(Dispatchers.Default) { runCatching { account.connect(credentials) } }
        withTimeout(10_000) { entered.await() }
        val queued = async(start = CoroutineStart.UNDISPATCHED) { runCatching { account.connect(credentials) } }
        account.disconnect()
        release.countDown()
        assertTrue(first.await().isFailure)
        assertTrue(queued.await().isFailure)
        assertEquals(1, calls.get())
        assertNull(secrets.get(key))
    }

    @Test
    fun suppliedGrantAuthorizesTheNormalDriveUploader() = runBlocking {
        val secrets = InMemorySecretStore()
        val file = Files.createTempFile("drive-user", ".epub").toFile()
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            val request = chain.request()
            val (status, body) = when (request.url.host) {
                "oauth2.googleapis.com" -> 200 to """{"access_token":"user-access","expires_in":3600}"""
                else -> {
                    assertEquals("Bearer user-access", request.header("Authorization"))
                    when (request.url.encodedPath) {
                        "/upload/drive/v3/files" -> 200 to "start"
                        "/drive-session" -> 200 to """{"id":"file-1"}"""
                        "/drive/v3/files/file-1/permissions" -> 200 to "{}"
                        else -> error("Unexpected Drive request")
                    }
                }
            }
            val response = Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(status).message("test")
                .body(body.toResponseBody("application/json".toMediaType()))
            if (request.url.encodedPath == "/upload/drive/v3/files") response.header("Location", "https://www.googleapis.com/drive-session")
            response.build()
        }.build()
        try {
            file.writeText("User-owned Drive upload")
            val account = GoogleDriveAccount(secrets, client)
            account.connect(credentials)
            val context = UploadContext(secrets, UploadSettings(hosts = listOf("gdrive")), client,
                googleOAuth = null, googleAccessToken = account::accessToken)
            val uploader = GoogleDriveUploader()
            assertTrue(uploader.isReady(context))
            assertTrue(uploader.upload(file, context).link.contains("file-1"))
        } finally { file.delete() }
    }
}
