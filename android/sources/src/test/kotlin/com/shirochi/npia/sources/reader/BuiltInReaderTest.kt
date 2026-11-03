package com.shirochi.npia.sources.reader

import com.shirochi.npia.sources.http.RoutedHttp
import com.shirochi.npia.sources.http.makeContext
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BuiltInReaderTest {
    @Test
    fun aSiteNoBuiltInScraperOwnsIsLeftToThePage() = runTest {
        val context = makeContext(RoutedHttp())
        val reply = Json.parseToJsonElement(
            CatalogTransport.handle("""{"op":"book","url":"https://example.com/book/1"}""", context.http, context.web, context),
        ).jsonObject
        assertEquals("true", reply["skip"]?.jsonPrimitive?.content)
        assertTrue(reply.containsKey("error"))
    }

    @Test
    fun withoutAContextABookRequestIsAnOrdinaryFetch() = runTest {
        val context = makeContext(RoutedHttp())
        // The Catalog always passes its context; a caller without one keeps the plain transport.
        val reply = Json.parseToJsonElement(
            CatalogTransport.handle("""{"op":"book","url":"not a web address"}""", context.http, context.web),
        ).jsonObject
        assertTrue(reply["error"]?.jsonPrimitive?.content.orEmpty().startsWith("invalid URL"))
    }
}
