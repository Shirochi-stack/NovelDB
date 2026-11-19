package com.shirochi.npia.sources.kpage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class KpageTicketsTest {
    @Test
    fun theScriptOnlyUsesTicketsAndNeverTheCashEndpoints() {
        val script = KpageTickets.script("52882308", "52894541", claim = true, preferred = listOf("RT06", "bad'); drop", "TT05"))
        assertTrue(script.startsWith("const seriesId = '52882308', productId = '52894541', claim = true, preferred = ['RT06','TT05'];"))
        for (path in listOf("/ticket/free_check", "/ticket/free'", "/ticket/ready_to_use", "/ticket/use'")) {
            assertTrue(path, script.contains(path))
        }
        for (path in listOf("ticket/buy", "buy_and_use", "set_buy", "cash")) assertFalse(path, script.contains(path))
        // Kotlin's string template must leave the JavaScript regular expression whole.
        assertTrue(script.contains("/^(RT|TT)\\d+\$/"))
    }

    @Test
    fun idsThatAreNotNumbersCannotReachTheScript() {
        val script = KpageTickets.script("12'; alert(1); '", "7", claim = false, preferred = emptyList())
        assertTrue(script.startsWith("const seriesId = '121', productId = '7', claim = false, preferred = [];"))
    }

    @Test
    fun theAnswerIsReadBack() {
        val used = KpageTickets.parse("""{"claimed":["RT06"],"tried":["RT06"],"used":"RT06","login":false,"error":"","giftError":""}""")
        assertEquals(listOf("RT06"), used.claimed)
        assertEquals("RT06", used.used)
        assertFalse(used.signedOut)
        assertEquals("a gift ticket", KpageTickets.describe(used.used))
        assertEquals("a wait-for-free ticket", KpageTickets.describe("RT05"))
        assertEquals("a rental ticket", KpageTickets.describe("RT01"))

        val none = KpageTickets.parse("""{"claimed":[],"tried":[],"used":"","login":false,"error":"no ticket available"}""")
        assertEquals("", none.used)
        assertEquals("no ticket available", none.error)
        assertTrue(KpageTickets.parse("""{"claimed":[],"used":"","login":true,"error":""}""").signedOut)
        assertEquals("unexpected answer", KpageTickets.parse("not json").error)
    }
}
