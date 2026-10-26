package com.shirochi.npia.upload

import com.shirochi.npia.core.UploadSettings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.File

class UploaderCatalogTest {
    @Test
    fun idsAreUnique() {
        val ids = UploaderCatalog.all.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
    }

    @Test
    fun defaultHostsAndAliasesResolve() {
        UploadSettings().hosts.forEach { assertNotNull(it, UploaderCatalog.uploader(it)) }
        assertEquals("sendnow", UploaderCatalog.uploader("sendcm")?.id)
    }

    @Test
    fun tooLargeFileIsRejected() {
        val file = File.createTempFile("npia", ".bin").apply { writeBytes(ByteArray(2048)); deleteOnExit() }
        val tiny = PendingUploader("tiny", "Tiny", 1024, null)
        assertThrows(UploadException.TooLarge::class.java) { tiny.check(file) }
    }
}
