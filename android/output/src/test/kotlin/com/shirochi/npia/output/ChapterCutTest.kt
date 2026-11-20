package com.shirochi.npia.output

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

class ChapterCutTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun book(name: String, chapters: List<Int>): List<File> {
        val pad = { n: Int -> n.toString().padStart(4, '0') }
        val epub = File(tmp.root, "$name.epub")
        ZipOutputStream(epub.outputStream()).use { zip ->
            fun add(path: String, text: String) {
                zip.putNextEntry(ZipEntry(path)); zip.write(text.encodeToByteArray()); zip.closeEntry()
            }
            add("mimetype", "application/epub+zip")
            add("META-INF/container.xml", """<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>""")
            add("OEBPS/images/cover.jpg", "cover")
            add("OEBPS/Text/info.xhtml", """<html><body><img src="../images/cover.jpg"/></body></html>""")
            add("OEBPS/Text/chapter_notice0001.xhtml", "<html><body>notice</body></html>")
            for (n in chapters) {
                add("OEBPS/images/${pad(n)}_01.webp", "page $n")
                add("OEBPS/Text/chapter${pad(n)}.xhtml", """<html><body><img src="../images/${pad(n)}_01.webp"/></body></html>""")
            }
            val items = chapters.joinToString("") {
                """<item id="chapter${pad(it)}" href="Text/chapter${pad(it)}.xhtml" media-type="application/xhtml+xml"/><item id="i$it" href="images/${pad(it)}_01.webp" media-type="image/webp"/>"""
            }
            add(
                "OEBPS/content.opf",
                """<package><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>""" +
                    """<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="cover-image" href="images/cover.jpg" media-type="image/jpeg"/>""" +
                    """<item id="info" href="Text/info.xhtml" media-type="application/xhtml+xml"/><item id="notice1" href="Text/chapter_notice0001.xhtml" media-type="application/xhtml+xml"/>$items</manifest>""" +
                    """<spine><itemref idref="info"/><itemref idref="notice1"/>${chapters.joinToString("") { """<itemref idref="chapter${pad(it)}"/>""" }}</spine></package>""",
            )
            add("OEBPS/toc.ncx", "<ncx><navMap>" + chapters.joinToString("") { """<navPoint id="n$it"><navLabel><text>$it</text></navLabel><content src="Text/chapter${pad(it)}.xhtml"/></navPoint>""" } + "</navMap></ncx>")
            add("OEBPS/nav.xhtml", "<html><body><nav><ol>" + chapters.joinToString("") { """<li><a href="Text/chapter${pad(it)}.xhtml">$it</a></li>""" } + "</ol></nav></body></html>")
        }
        val cbz = File(tmp.root, "$name.cbz")
        ZipOutputStream(cbz.outputStream()).use { zip ->
            zip.putNextEntry(ZipEntry("0000_cover.jpg")); zip.write(1); zip.closeEntry()
            for (n in chapters) {
                zip.putNextEntry(ZipEntry("${pad(n)}_Chapter/0001.png")); zip.write(n); zip.closeEntry()
            }
        }
        val folder = File(tmp.root, name)
        for (n in chapters) File(folder, "${pad(n)} Chapter $n").apply { mkdirs() }.resolve("001.webp").writeText("page $n")
        return listOf(epub, cbz, folder)
    }

    @Test
    fun cutsTheChaptersOutOfTheSavedFiles() {
        val result = ChapterCut.cut(book("Book (1,3-5)", listOf(1, 3, 4, 5)), 3, 4, "")
        assertEquals(emptyList<String>(), result.failed)
        assertEquals(listOf("Book (3-4).epub", "Book (3-4).cbz", "Book (3-4)"), result.files.map { it.name })
        ZipFile(result.files[0]).use { zip ->
            val names = zip.entries().toList().map { it.name }
            assertEquals("mimetype", names.first())
            assertEquals(ZipEntry.STORED, zip.getEntry("mimetype").method)
            assertEquals(
                listOf("OEBPS/Text/info.xhtml", "OEBPS/Text/chapter_notice0001.xhtml", "OEBPS/Text/chapter0003.xhtml", "OEBPS/Text/chapter0004.xhtml"),
                names.filter { it.startsWith("OEBPS/Text/") },
            )
            assertEquals(setOf("OEBPS/images/cover.jpg", "OEBPS/images/0003_01.webp", "OEBPS/images/0004_01.webp"), names.filter { "/images/" in it }.toSet())
            val read = { name: String -> zip.getInputStream(zip.getEntry(name)).use { it.readBytes().decodeToString() } }
            val opf = read("OEBPS/content.opf")
            assertTrue(Regex("href=\"([^\"]+)\"").findAll(opf).all { "OEBPS/" + it.groupValues[1] in names })
            assertEquals(listOf("info", "notice1", "chapter0003", "chapter0004"), Regex("idref=\"([^\"]+)\"").findAll(opf).map { it.groupValues[1] }.toList())
            assertEquals(2, Regex("<navPoint").findAll(read("OEBPS/toc.ncx")).count())
            assertEquals(2, Regex("<li>").findAll(read("OEBPS/nav.xhtml")).count())
        }
        ZipFile(result.files[1]).use { zip ->
            assertEquals(listOf("0000_cover.jpg", "0003_Chapter/0001.png", "0004_Chapter/0001.png"), zip.entries().toList().map { it.name })
        }
        assertEquals(listOf("0003 Chapter 3", "0004 Chapter 4"), result.files[2].list()!!.sorted())
    }

    @Test
    fun reportsWhatCannotBeCutAndKeepsFilesThatAlreadyAreTheRange() {
        val files = book("Book (1-2)", listOf(1, 2))
        val none = ChapterCut.cut(files, 7, 7, "")
        assertEquals(emptyList<File>(), none.files)
        assertEquals(3, none.failed.size)
        assertTrue(tmp.root.list()!!.none { "(7)" in it })
        val pdf = File(tmp.root, "Book.pdf").apply { writeText("%PDF") }
        assertEquals(listOf("Book.pdf"), ChapterCut.cut(listOf(pdf), 1, 1, "").failed)
        assertEquals(files, ChapterCut.cut(files, 1, 2, "").files)
        assertEquals("Book (1,4).epub", ChapterCut.cut(book("Other", listOf(1, 2, 4)).take(1), 1, 4, "1,4").files.single().name.replace("Other", "Book"))
    }
}
