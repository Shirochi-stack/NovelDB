package com.shirochi.npia.net

import com.shirochi.npia.core.AppBuild
import com.shirochi.npia.core.AppUpdateUrls
import com.shirochi.npia.core.UpdateAsset
import com.shirochi.npia.core.UpdateOffer
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

class AppUpdateClientTest {
    private lateinit var server: MockWebServer
    @get:Rule val folder = TemporaryFolder()

    private val client = AppUpdateClient()
    private val payload = ByteArray(300_000) { (it * 31 + 7).toByte() }

    @Before
    fun setUp() {
        server = MockWebServer().apply { start() }
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun urls() = AppUpdateUrls(server.url("/releases").toString())

    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun asset(bytes: ByteArray = payload, size: Long = bytes.size.toLong(), sha256: String = sha(bytes), build: Long = 2000) =
        UpdateAsset("NovelDB-universal.apk", "universal", "universal", "apk", build, "hotfix3", "abc", size, sha256)

    private fun offer(asset: UpdateAsset = asset()) = UpdateOffer("v98", "https://example.com/v98", asset)

    private fun manifestJson(build: Long = 2000, extra: String = "") = """
        {"schema": 1, "platform": "android", "tag": "v98", "release_url": "https://example.com/v98",
         "assets": [{"name": "NovelDB-universal.apk", "variant": "universal", "arch": "universal", "kind": "apk",
                     "build": $build, "label": "none", "commit": "abc", "size": ${payload.size}, "sha256": "${sha(payload)}"}$extra]}
    """.trimIndent()

    private fun serve(bytes: ByteArray, chunked: Boolean = false) {
        val response = MockResponse()
        if (chunked) response.setChunkedBody(Buffer().write(bytes), 4096) else response.setBody(Buffer().write(bytes))
        server.enqueue(response)
    }

    private fun filesIn(dir: File) = dir.listFiles().orEmpty().map { it.name }

    // Checking

    @Test
    fun checkFindsANewerBuildAtTheContractAddress() = runTest {
        server.enqueue(MockResponse().setBody(manifestJson(build = 2000)))
        val result = client.check(AppBuild(1000, "v97"), urls())

        val offer = (result as UpdateCheck.Available).offer
        assertEquals("v98", offer.tag)
        assertEquals(2000L, offer.build)
        assertEquals("/releases/latest/download/update-android.json", server.takeRequest().path)
    }

    @Test
    fun checkFollowsRedirectsLikeGithubDoes() = runTest {
        server.enqueue(MockResponse().setResponseCode(302).addHeader("Location", server.url("/elsewhere/update-android.json").toString()))
        server.enqueue(MockResponse().setBody(manifestJson(build = 2000)))
        assertTrue(client.check(AppBuild(1000, "v97"), urls()) is UpdateCheck.Available)
        assertEquals(2, server.requestCount)
    }

    @Test
    fun checkSaysUpToDateWhenTheBuildIsNotOlder() = runTest {
        repeat(2) { server.enqueue(MockResponse().setBody(manifestJson(build = 2000))) }
        assertEquals(UpdateCheck.UpToDate, client.check(AppBuild(2000, "v98"), urls()))
        assertEquals(UpdateCheck.UpToDate, client.check(AppBuild(3000, "v99"), urls()))
    }

    @Test
    fun aMissingManifestBadManifestOrMissingEntryIsNoInformation() = runTest {
        server.enqueue(MockResponse().setResponseCode(404))
        assertEquals(UpdateCheck.NoInformation, client.check(AppBuild(1000), urls()))
        server.enqueue(MockResponse().setBody("<html>not json</html>"))
        assertEquals(UpdateCheck.NoInformation, client.check(AppBuild(1000), urls()))
        server.enqueue(MockResponse().setBody(manifestJson().replace("\"schema\": 1", "\"schema\": 2")))
        assertEquals(UpdateCheck.NoInformation, client.check(AppBuild(1000), urls()))
        server.enqueue(MockResponse().setBody(manifestJson().replace("\"kind\": \"apk\"", "\"kind\": \"ipa\"")))
        assertEquals(UpdateCheck.NoInformation, client.check(AppBuild(1000), urls()))
        server.enqueue(MockResponse().setBody("x".repeat((AppUpdateClient.MAX_MANIFEST_BYTES + 10).toInt())))
        assertEquals(UpdateCheck.NoInformation, client.check(AppBuild(1000), urls()))
    }

    @Test
    fun serverErrorsAndUnreachableServersAreFailures() = runTest {
        server.enqueue(MockResponse().setResponseCode(503))
        val error = client.check(AppBuild(1000), urls()) as UpdateCheck.Failed
        assertEquals("HTTP 503", error.message)

        val dead = AppUpdateUrls("http://127.0.0.1:1")
        assertTrue(client.check(AppBuild(1000), dead) is UpdateCheck.Failed)
    }

    @Test
    fun aDevelopmentBuildDoesNotEvenAsk() = runTest {
        assertEquals(UpdateCheck.Development, client.check(AppBuild(), urls()))
        assertEquals(0, server.requestCount)
    }

    // Downloading and verifying

    @Test
    fun aMatchingFileIsKeptAndReportsProgress() = runTest {
        serve(payload, chunked = true)
        val progress = mutableListOf<Pair<Long, Long>>()
        val dir = folder.newFolder("updates")

        val file = client.download(offer(), server.url("/a.apk").toString(), dir) { done, total -> progress += done to total }

        assertEquals("NovelDB-universal.apk", file.name)
        assertTrue(file.readBytes().contentEquals(payload))
        assertEquals(listOf("NovelDB-universal.apk"), filesIn(dir))
        assertEquals(payload.size.toLong() to payload.size.toLong(), progress.last())
        assertTrue(progress.size > 1)
        assertTrue(progress.zipWithNext().all { (a, b) -> a.first < b.first })
        assertTrue(progress.all { it.second == payload.size.toLong() })
    }

    @Test
    fun aWrongChecksumDeletesTheFile() = runTest {
        val tampered = payload.clone().also { it[1234] = (it[1234] + 1).toByte() }
        serve(tampered)
        val dir = folder.newFolder("updates")

        val error = runCatching { client.download(offer(), server.url("/a.apk").toString(), dir) }.exceptionOrNull()

        assertTrue(error is UpdateVerificationException)
        assertEquals("The download did not match its checksum; nothing was installed.", error!!.message)
        assertEquals(emptyList<String>(), filesIn(dir))
    }

    @Test
    fun aShorterFileThanTheManifestSaysDeletesTheFile() = runTest {
        val short = payload.copyOf(payload.size - 10)
        serve(short, chunked = true) // no Content-Length to catch it early
        val dir = folder.newFolder("updates")

        val error = runCatching { client.download(offer(), server.url("/a.apk").toString(), dir) }.exceptionOrNull()

        assertTrue(error is UpdateVerificationException)
        assertEquals(emptyList<String>(), filesIn(dir))
    }

    @Test
    fun aLongerFileThanTheManifestSaysIsRefusedAndDeleted() = runTest {
        val long = payload + ByteArray(50) { 9 }
        serve(long, chunked = true)
        val dir = folder.newFolder("updates")

        val error = runCatching { client.download(offer(), server.url("/a.apk").toString(), dir) }.exceptionOrNull()

        assertTrue(error is UpdateVerificationException)
        assertEquals(emptyList<String>(), filesIn(dir))
    }

    @Test
    fun aManifestSizeThatDisagreesWithTheRightFileIsAMismatchToo() = runTest {
        serve(payload)
        val dir = folder.newFolder("updates")

        // The checksum is right but the size is not what the manifest promises.
        val error = runCatching { client.download(offer(asset(size = payload.size + 1L)), server.url("/a.apk").toString(), dir) }.exceptionOrNull()

        assertTrue(error is UpdateVerificationException)
        assertEquals(emptyList<String>(), filesIn(dir))
    }

    @Test
    fun anHttpErrorLeavesNothingBehind() = runTest {
        server.enqueue(MockResponse().setResponseCode(404))
        val dir = folder.newFolder("updates")

        val error = runCatching { client.download(offer(), server.url("/a.apk").toString(), dir) }.exceptionOrNull()

        assertEquals("HTTP 404", error!!.message)
        assertFalse(error is UpdateVerificationException)
        assertEquals(emptyList<String>(), filesIn(dir))
    }

    @Test
    fun aStaleFileOfThePreviousAttemptIsReplaced() = runTest {
        val dir = folder.newFolder("updates")
        File(dir, "NovelDB-universal.apk").writeBytes(ByteArray(10))
        File(dir, "NovelDB-universal.apk.part").writeBytes(ByteArray(10))
        serve(payload)

        val file = client.download(offer(), server.url("/a.apk").toString(), dir)

        assertTrue(file.readBytes().contentEquals(payload))
        assertEquals(listOf("NovelDB-universal.apk"), filesIn(dir))
    }

    @Test
    fun cancellingStopsTheDownloadAndDeletesTheFile() {
        // A server that sends the first part and then stalls, so the download is mid-file when cancelled.
        server.enqueue(MockResponse().setBody(Buffer().write(payload)).throttleBody(20_000, 100, TimeUnit.MILLISECONDS))
        val dir = folder.newFolder("updates")
        val started = CompletableDeferred<Unit>()

        runBlocking(Dispatchers.Default) {
            val download = async {
                client.download(offer(), server.url("/a.apk").toString(), dir) { done, _ -> if (done > 0) started.complete(Unit) }
            }
            withTimeout(10_000) { started.await() }
            download.cancelAndJoin()
            assertTrue(download.isCancelled)
            val error = runCatching { download.await() }.exceptionOrNull()
            assertTrue(error is CancellationException)
        }

        assertEquals(emptyList<String>(), filesIn(dir))
    }

    // Verifying a stored file

    @Test
    fun verifyAcceptsTheRightFileAndDeletesAWrongOne() {
        val good = folder.newFile("good.apk").also { it.writeBytes(payload) }
        AppUpdateClient.verify(good, asset())
        assertTrue(good.exists())
        assertTrue(AppUpdateClient.matches(good, asset()))

        val bad = folder.newFile("bad.apk").also { it.writeBytes(payload.reversedArray()) }
        val error = runCatching { AppUpdateClient.verify(bad, asset()) }.exceptionOrNull()
        assertTrue(error is UpdateVerificationException)
        assertFalse(bad.exists())

        val missing = File(folder.root, "missing.apk")
        assertFalse(AppUpdateClient.matches(missing, asset()))
        assertNotNull(runCatching { AppUpdateClient.verify(missing, asset()) }.exceptionOrNull())
    }

    @Test
    fun checksumComparisonIgnoresCase() {
        val file = folder.newFile("case.apk").also { it.writeBytes(payload) }
        assertTrue(AppUpdateClient.matches(file, asset(sha256 = sha(payload).uppercase())))
    }
}
