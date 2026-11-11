package com.shirochi.npia.sources.reader

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.Base64
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class CatalogDataTest {
    @Test fun optionalDataIsReadOnlyAfterInstallationAndRemovedSeparately() {
        val root = Files.createTempDirectory("catalogue-data-test").toFile()
        val previous = CatalogData.directory
        CatalogData.directory = root
        fun request(action: String, version: String = "fixture", key: String = "manifest") =
            Json.parseToJsonElement(CatalogData.handle(buildJsonObject {
                put("action", action)
                put("version", version)
                put("key", key)
            })).jsonObject
        try {
            assertEquals("", request("status").getValue("version").jsonPrimitive.content)
            val folder = File(root, "fixture").apply { mkdirs() }
            ZipOutputStream(File(folder, "instant-catalog-0.zip").outputStream()).use {
                it.putNextEntry(ZipEntry("manifest.json.gz"))
                it.write("catalogue fixture".toByteArray())
                it.closeEntry()
            }
            File(root, "installed.json").writeText("""{"revision":"fixture","books":12,"built":"2026-11-11"}""")
            val bytes = Base64.getDecoder().decode(request("read").getValue("base64").jsonPrimitive.content)
            assertEquals("catalogue fixture", bytes.decodeToString())
            assertTrue(request("read", "../fixture").isEmpty())
            assertEquals("", request("remove").getValue("version").jsonPrimitive.content)
            assertFalse(root.exists())
        } finally {
            CatalogData.directory = previous
            root.deleteRecursively()
        }
    }
}
