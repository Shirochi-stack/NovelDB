package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.ImageRef
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class LibraryLiveTest {
    @Test fun metadataDeltasAndReadyBodiesSurviveCompletion() {
        val session = LibraryLive.Session("https://source.test/book")
        try {
            val refs = (1..3).map { ChapterRef("$it", it, "Chapter $it") }
            session.book(refs)
            val initial = session.snapshot("", 0, null)
            assertEquals(3, initial["chapters"]!!.jsonArray.size)
            val version = initial["version"]!!.jsonPrimitive.int
            session.chapter(ChapterResult.Content(ChapterContent(refs[1], "<p>Text\n\nNext</p><img src='images/a.png'>",
                listOf(ImageRef("https://source.test/a.png", "images/a.png", byteArrayOf(1, 2, 3))))))
            val delta = session.snapshot(session.id, version, null)
            assertFalse(delta.containsKey("chapters"))
            assertEquals("1", delta["states"]!!.jsonArray.single().jsonObject["key"]!!.jsonPrimitive.content)
            val body = session.snapshot(session.id, 0, "1")["html"]!!.jsonPrimitive.content
            assertTrue(body.contains("Text\n\nNext"))
            assertTrue(body.contains("data:image/png;base64,AQID"))
            assertTrue(session.snapshot(session.id, 0, "../1").containsKey("error"))
            session.finish(saved = true, spineCount = 5)
            val finished = session.snapshot("", 0, null)
            assertFalse(finished["active"]!!.jsonPrimitive.boolean)
            assertEquals(2, finished["savedOffset"]!!.jsonPrimitive.int)
            assertEquals(2, finished["states"]!!.jsonArray.count { it.jsonObject["state"]!!.jsonPrimitive.content == "unavailable" })
            assertEquals(body, session.snapshot(session.id, 0, "1")["html"]!!.jsonPrimitive.content)
        } finally {
            session.close()
        }
    }
}
