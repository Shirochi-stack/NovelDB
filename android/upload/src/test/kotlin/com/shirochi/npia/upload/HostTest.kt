package com.shirochi.npia.upload

import kotlinx.coroutines.runBlocking
import okio.Buffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class MultipartTest : UploadTestBase() {
    @Test
    fun bodyIsHeadFileTail() {
        val file = makeFile("소설 \"1\".epub", bytes = 3 * 1024 + 7)
        val form = MultipartForm("fileToUpload", file, fields = listOf("reqtype" to "fileupload"), boundary = "B")
        val body = form.body()
        val out = Buffer()
        body.writeTo(out)

        val expectedHead = "--B\r\nContent-Disposition: form-data; name=\"reqtype\"\r\n\r\nfileupload\r\n" +
            "--B\r\nContent-Disposition: form-data; name=\"fileToUpload\"; filename=\"소설 %221%22.epub\"\r\n" +
            "Content-Type: application/octet-stream\r\n\r\n"
        val expected = expectedHead.toByteArray(Charsets.UTF_8) + file.readBytes() + "\r\n--B--\r\n".toByteArray(Charsets.UTF_8)

        assertArrayEquals(expected, out.readByteArray())
        assertEquals(expected.size.toLong(), body.contentLength())
        assertEquals("multipart/form-data; boundary=B", form.contentType)
    }

    @Test
    fun progressCountsEveryByte() {
        val file = makeFile(bytes = 200_000)
        val box = ProgressBox()
        val body = MultipartForm("file", file).body(box::add)
        body.writeTo(Buffer())
        assertEquals(body.contentLength(), box.last?.sentBytes)
        assertEquals(body.contentLength(), box.last?.totalBytes)
        assertTrue(box.all.size > 3)
    }

    @Test
    fun formEncodingEscapesReservedAndUnicode() {
        assertEquals("a%20b=x%26y%3Dz%2F%C3%A9~", formEncoded(listOf("a b" to "x&y=z/é~")))
    }

    @Test
    fun uploadNameIsNfc() {
        val decomposed = String(intArrayOf(0x1112, 0x1161, 0x11AB), 0, 3) + ".epub"
        val composed = String(intArrayOf(0xD55C), 0, 1) + ".epub"
        assertEquals(composed, uploadName(java.io.File(decomposed)))
    }
}

class HostTest : UploadTestBase() {
    // temp.sh

    @Test
    fun tempShUploadsMultipartAndReadsPlainLink() = runBlocking<Unit> {
        val file = makeFile()
        handler = { text("https://temp.sh/AbCdE/book.epub\n") }
        val box = ProgressBox()
        val result = TempShUploader().upload(file, context(), box::add)

        assertEquals("https://temp.sh/AbCdE/book.epub", result.link)
        assertEquals(fixedNow + 3 * 86_400_000L, result.expiresAt)
        val request = seen.first()
        assertEquals(TempShUploader.ENDPOINT, request.url)
        assertEquals("POST", request.method)
        assertTrue(request.header("Content-Type")!!.startsWith("multipart/form-data; boundary="))
        assertTrue(request.text.contains("name=\"file\"; filename=\"book.epub\""))
        assertEquals(box.last?.totalBytes, box.last?.sentBytes)
    }

    @Test
    fun tempShRejectsNonLink() {
        assertThrows(UploadException.Rejected::class.java) { TempShUploader.parse(reply("<html>error</html>")) }
    }

    // Gofile

    @Test
    fun gofileSendsTokenWhenPresent() = runBlocking<Unit> {
        val file = makeFile()
        handler = { json("""{"status":"ok","data":{"downloadPage":"https://gofile.io/d/Xy9","id":"f"}}""") }
        val result = GofileUploader().upload(file, context(secrets = mapOf(UploadSecrets.GOFILE_TOKEN to "tok")))
        assertEquals("https://gofile.io/d/Xy9", result.link)
        val request = seen.first()
        assertEquals("https://upload.gofile.io/uploadfile", request.url.toString())
        assertEquals("Bearer tok", request.header("Authorization"))
    }

    @Test
    fun gofileGuestSendsNoAuthorization() = runBlocking<Unit> {
        handler = { json("""{"status":"ok","data":{"downloadPage":"https://gofile.io/d/G"}}""") }
        GofileUploader().upload(makeFile(), context())
        assertNull(seen.first().header("Authorization"))
    }

    @Test
    fun gofileErrorStatus() {
        val error = assertThrows(UploadException.Rejected::class.java) {
            GofileUploader.parse(reply("""{"status":"error-rateLimit","data":{}}"""))
        }
        assertEquals(UploadException.Rejected("Gofile", "error-rateLimit"), error)
    }

    // Catbox

    @Test
    fun catboxParsesLinkAndSendsUserHash() = runBlocking<Unit> {
        val file = makeFile()
        handler = { text("https://files.catbox.moe/abc123.epub") }
        val result = CatboxUploader().upload(file, context(secrets = mapOf(UploadSecrets.CATBOX_USER_HASH to "h")))
        assertEquals("https://files.catbox.moe/abc123.epub", result.link)
        assertNull(result.expiresAt)
        val body = seen.first().text
        assertTrue(body.contains("name=\"reqtype\"\r\n\r\nfileupload\r\n"))
        assertTrue(body.contains("name=\"userhash\"\r\n\r\nh\r\n"))
        assertTrue(body.contains("name=\"fileToUpload\"; filename=\"book.epub\""))
    }

    // Pixeldrain

    @Test
    fun pixeldrainRequestUsesPutAndBasicAuth() {
        assertEquals("https://pixeldrain.com/api/file/My%20Book%20%231%3F.epub", PixeldrainUploader.url("My Book #1?.epub").toString())
        val file = makeFile(bytes = 42)
        val request = PixeldrainUploader.request(file, apiKey = "key")
        assertEquals("PUT", request.method)
        assertEquals("https://pixeldrain.com/api/file/book.epub", request.url.toString())
        assertEquals("Basic " + Base64.getEncoder().encodeToString(":key".toByteArray()), request.header("Authorization"))
        assertEquals(42L, request.body?.contentLength())
    }

    @Test
    fun pixeldrainParse() {
        assertEquals("https://pixeldrain.com/u/Ab12", PixeldrainUploader.parse(reply("""{"success":true,"id":"Ab12"}""", status = 201)))
        val tooBig = assertThrows(UploadException.Rejected::class.java) {
            PixeldrainUploader.parse(reply("""{"success":false,"value":"file_too_large","message":"too big"}""", status = 413))
        }
        assertEquals(UploadException.Rejected("Pixeldrain", "too big"), tooBig)
        val unauthorized = assertThrows(UploadException.NotSignedIn::class.java) {
            PixeldrainUploader.parse(reply("""{"success":false}""", status = 401))
        }
        assertEquals(UploadException.NotSignedIn("Pixeldrain"), unauthorized)
    }

    @Test
    fun pixeldrainNeedsKey() {
        assertFalse(PixeldrainUploader().isReady(context()))
        assertTrue(PixeldrainUploader().isReady(context(secrets = mapOf(UploadSecrets.PIXELDRAIN_API_KEY to "k"))))
    }

    @Test
    fun pixeldrainUploadsRawFile() = runBlocking<Unit> {
        val file = makeFile()
        handler = { json("""{"success":true,"id":"Zz"}""", status = 201) }
        val result = PixeldrainUploader().upload(file, context(secrets = mapOf(UploadSecrets.PIXELDRAIN_API_KEY to "k")))
        assertEquals("https://pixeldrain.com/u/Zz", result.link)
        assertEquals("PUT", seen.first().method)
        assertArrayEquals(file.readBytes(), seen.first().body)
    }

    // tmpfiles

    @Test
    fun tmpfilesUpgradesToHttpsAndSetsExpiry() = runBlocking<Unit> {
        val file = makeFile()
        handler = { json("""{"status":"success","data":{"url":"http://tmpfiles.org/123456/book.epub"}}""") }
        val result = TmpfilesUploader().upload(file, context())
        assertEquals("https://tmpfiles.org/123456/book.epub", result.link)
        assertEquals(fixedNow + 172_800_000L, result.expiresAt)
        assertTrue(seen.first().text.contains("name=\"expire\"\r\n\r\n172800\r\n"))
    }

    // KrakenFiles

    @Test
    fun krakenFilesAsksForServerThenUploads() = runBlocking<Unit> {
        val file = makeFile()
        handler = { request ->
            if (request.url == KrakenFilesUploader.SERVER_LOOKUP) {
                json("""{"data":{"url":"https://s7.krakenfiles.com/_uploader/upload/xyz","serverAccessToken":"t"}}""")
            } else if (request.url.toString() == "https://s7.krakenfiles.com/_uploader/gallery/upload") {
                json("""{"files":[{"name":"book.epub","url":"/view/Qx12/file.html","hash":"Qx12"}]}""")
            } else {
                unexpected("unexpected ${request.url}")
            }
        }
        val result = KrakenFilesUploader().upload(file, context())
        assertEquals("https://krakenfiles.com/view/Qx12/file.html", result.link)
        assertEquals(2, seen.size)
        assertTrue(seen.last().text.contains("name=\"files[]\"; filename=\"book.epub\""))
    }

    @Test
    fun krakenFilesFileError() {
        val error = assertThrows(UploadException.Rejected::class.java) {
            KrakenFilesUploader.parse(reply("""{"files":[{"error":"File type not allowed"}]}"""))
        }
        assertEquals(UploadException.Rejected("KrakenFiles", "File type not allowed"), error)
    }

    // Send.now

    @Test
    fun sendNowFindsServerOnHomePage() {
        val html = """<form enctype="multipart/form-data" action="https://ab12cd.send.now/cgi-bin/upload.cgi?upload_type=file" method="post">"""
        assertEquals(
            "https://ab12cd.send.now/cgi-bin/upload.cgi?upload_type=file&utype=anon",
            SendNowUploader.uploadEndpoint(reply(html)).toString(),
        )
        assertThrows(UploadException.Rejected::class.java) { SendNowUploader.uploadEndpoint(reply("<html></html>")) }
    }

    @Test
    fun sendNowParse() {
        assertEquals("https://send.now/k3j4h5", SendNowUploader.parse(reply("""[{"file_code":"k3j4h5","file_status":"OK"}]""")))
        assertThrows(UploadException.Rejected::class.java) {
            SendNowUploader.parse(reply("""[{"file_code":"undef","file_status":"too big"}]"""))
        }
    }

    @Test
    fun sendNowUploadsToServerFromHomePage() = runBlocking<Unit> {
        handler = { request ->
            when (request.url.host) {
                "send.now" -> text("""<a href="https://zz99.send.now/cgi-bin/upload.cgi?x">""")
                "zz99.send.now" -> json("""[{"file_code":"q1","file_status":"OK"}]""")
                else -> unexpected("unexpected ${request.url}")
            }
        }
        val result = SendNowUploader().upload(makeFile(), context())
        assertEquals("https://send.now/q1", result.link)
        assertEquals("anon", seen.last().url.queryParameter("utype"))
        assertTrue(seen.last().text.contains("name=\"file_0\"; filename=\"book.epub\""))
    }
}
