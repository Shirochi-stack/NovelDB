package com.shirochi.npia.app

import com.shirochi.npia.app.ui.Accent
import com.shirochi.npia.app.ui.ThemeMode
import org.junit.Assert.assertEquals
import org.junit.Test

class ThemeTest {
    @Test
    fun halgakosRoseIsTheDefaultAccent() {
        assertEquals(Accent.HALGAKOS_ROSE, Accent.of(null))
        assertEquals(Accent.HALGAKOS_ROSE, Accent.of("unknown"))
        assertEquals(Accent.LIBRARY_VIOLET, Accent.of("library_violet"))
    }

    @Test
    fun themeModesRoundTrip() {
        ThemeMode.entries.forEach { assertEquals(it, ThemeMode.of(it.raw)) }
        assertEquals(ThemeMode.SYSTEM, ThemeMode.of(null))
    }
}
