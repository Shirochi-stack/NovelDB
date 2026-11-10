package com.shirochi.npia.app

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BackNavigationTest {
    @Test fun repeatedBackAtRootKeepsTheActivityOpen() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            InstrumentationRegistry.getInstrumentation().waitForIdleSync()
            repeat(3) {
                scenario.onActivity { it.onBackPressedDispatcher.onBackPressed() }
                InstrumentationRegistry.getInstrumentation().waitForIdleSync()
            }
            scenario.onActivity { assertFalse(it.isFinishing) }
        }
    }

    @Test fun backUnwindsPagesAndTabsAndStaysAtRoot() {
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            val model = AppViewModel(ApplicationProvider.getApplicationContext<Application>())
            assertFalse(model.back())
            assertEquals(Tab.ADD, model.tab)
            model.select(Tab.CATALOG)
            model.select(Tab.SETTINGS)
            model.push(Route.General)
            assertTrue(model.back())
            assertEquals(Tab.SETTINGS, model.tab)
            assertTrue(model.stack(Tab.SETTINGS).isEmpty())
            assertTrue(model.back())
            assertEquals(Tab.CATALOG, model.tab)
            assertTrue(model.back())
            assertEquals(Tab.ADD, model.tab)
            repeat(3) { assertFalse(model.back()) }
            assertEquals(Tab.ADD, model.tab)
        }
    }
}
