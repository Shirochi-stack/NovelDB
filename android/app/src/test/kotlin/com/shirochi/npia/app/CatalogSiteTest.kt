package com.shirochi.npia.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CatalogSiteTest {
    @Test
    fun acceptsOnlyFullHttpAddresses() {
        assertEquals("https://example.com/books", validCatalogUrl(" https://example.com/books "))
        assertEquals("http://192.168.1.5:8000/", validCatalogUrl("http://192.168.1.5:8000/"))
        assertNull(validCatalogUrl("https://"))
        assertNull(validCatalogUrl("https:// example.com"))
        assertNull(validCatalogUrl("example.com"))
        assertNull(validCatalogUrl("file:///sdcard/index.html"))
        assertNull(validCatalogUrl(null))
    }
}
