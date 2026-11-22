package com.shirochi.npia.sources.reader

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.Base64

/** The reader's page reads a Library book in parts: a comic's EPUB is far more than it can take at once. */
class CatalogTransportFileTest {
    @get:Rule
    val temp = TemporaryFolder()

    @After
    fun forgetFolder() {
        CatalogTransport.libraryFolder = null
    }

    private fun reply(text: String) = Json.parseToJsonElement(text) as JsonObject
    private fun JsonObject.long(name: String) = (this[name] as? JsonPrimitive)?.longOrNull
    private fun JsonObject.text(name: String) = (this[name] as? JsonPrimitive)?.contentOrNull
    private fun JsonObject.bytes() = Base64.getDecoder().decode(text("responseBase64"))

    @Test
    fun aBigBookIsHandedOverInTheAskedParts() {
        val library = temp.newFolder("library")
        CatalogTransport.libraryFolder = library
        val size = 9 * 1024 * 1024 + 123
        val data = ByteArray(size) { (it % 251).toByte() }
        val book = File(library, "comic.epub").apply { writeBytes(data) }
        val url = book.toURI().toString()

        // The book's end (a negative offset counts from the end), where it starts, and the size.
        val tail = reply(CatalogTransport.libraryFile(url, -1000, 1000))
        assertEquals(size.toLong(), tail.long("size"))
        assertEquals(size - 1000L, tail.long("offset"))
        assertArrayEquals(data.copyOfRange(size - 1000, size), tail.bytes())

        val part = reply(CatalogTransport.libraryFile(url, 4096, 300))
        assertEquals(4096L, part.long("offset"))
        assertArrayEquals(data.copyOfRange(4096, 4396), part.bytes())

        // Asked for more than there is: what is there. Past the end: nothing.
        val last = reply(CatalogTransport.libraryFile(url, size - 10L, 500))
        assertArrayEquals(data.copyOfRange(size - 10, size), last.bytes())
        assertEquals(0, reply(CatalogTransport.libraryFile(url, size + 50L, 10)).bytes().size)
        assertEquals(0, reply(CatalogTransport.libraryFile(url, -1000, 0)).bytes().size)

        // No part asked for (a TXT, or an older page): the whole file, without an offset.
        val whole = reply(CatalogTransport.libraryFile(url))
        assertNull(whole.long("offset"))
        assertEquals(size, whole.bytes().size)
    }

    @Test
    fun aSmallBookComesWholeAndOnlyLibraryBooksAreHandedOver() {
        val library = temp.newFolder("library")
        CatalogTransport.libraryFolder = library
        val small = File(library, "novel.epub").apply { writeBytes(ByteArray(5000) { 7 }) }
        // One request reads a small book, whatever part was asked for (no offset: the page keeps it).
        val reply = reply(CatalogTransport.libraryFile(small.toURI().toString(), -262144, 262144))
        assertNull(reply.long("offset"))
        assertEquals(5000L, reply.long("size"))
        assertEquals(5000, reply.bytes().size)

        val outside = temp.newFile("elsewhere.epub").apply { writeBytes(ByteArray(10)) }
        assertTrue(reply(CatalogTransport.libraryFile(outside.toURI().toString(), 0, 10)).text("error")!!.contains("only EPUB and TXT files in the Library"))
        val other = File(library, "notes.pdf").apply { writeBytes(ByteArray(10)) }
        assertTrue(reply(CatalogTransport.libraryFile(other.toURI().toString(), 0, 10)).text("error") != null)
    }
}
