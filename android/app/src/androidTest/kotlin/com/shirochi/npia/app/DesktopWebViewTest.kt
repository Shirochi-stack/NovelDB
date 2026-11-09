package com.shirochi.npia.app

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.webkit.WebViewFeature
import com.shirochi.npia.core.LocalResponse
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.webengine.AndroidWebEngine
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DesktopWebViewTest {
    @Test
    fun windowsDesktopModeUsesMatchingClientHintsAfterNavigation() = runBlocking {
        assumeTrue(WebViewFeature.isFeatureSupported(WebViewFeature.USER_AGENT_METADATA))
        val app = ApplicationProvider.getApplicationContext<Application>()
        val page = AndroidWebEngine(app).newPage("desktop-test")
        try {
            page.emulateDesktop(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                1280, 900,
            )
            page.serveLocal("https://desktop.example/") { _, _ -> LocalResponse("text/html", "<p>Desktop fixture</p>".toByteArray()) }
            for (path in listOf("first", "second")) {
                page.load("https://desktop.example/$path", WebLoadState.LOAD, 30)
                assertEquals("\"Windows\"", page.evaluateJson("return navigator.userAgentData.platform;"))
                assertEquals("false", page.evaluateJson("return navigator.userAgentData.mobile;"))
                assertEquals("\"x86\"", page.evaluateJson("return (await navigator.userAgentData.getHighEntropyValues(['architecture'])).architecture;"))
                assertEquals("\"\"", page.evaluateJson("return (await navigator.userAgentData.getHighEntropyValues(['model'])).model;"))
            }
        } finally {
            page.close()
        }
    }
}
