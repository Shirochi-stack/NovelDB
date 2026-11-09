package com.shirochi.npia.app

import android.content.Context
import android.content.Intent
import androidx.lifecycle.ViewModelProvider
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.shirochi.npia.core.SecretKey
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class GoogleDriveCredentialsTest {
    @Test
    fun validatedUserGrantIsEncryptedAndRestored() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val secrets = EncryptedSecretStore(context)
        val key = SecretKey("upload.gdrive.credentials")
        val previous = secrets.get(key)
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("test")
                .body("""{"access_token":"test-access","expires_in":3600}""".toResponseBody("application/json".toMediaType())).build()
        }.build()
        try {
            val account = GoogleDriveAccount(secrets, client)
            account.connect(GoogleDriveCredentials("1234-user.apps.googleusercontent.com", "private-client-secret", "private-refresh-token"))
            assertTrue(account.signedIn.value)
            val xml = File(context.applicationInfo.dataDir, "shared_prefs/npia_secrets.xml")
            withTimeout(10_000) { while (!xml.exists()) delay(50) }
            delay(200)
            assertFalse(xml.readText().contains("private-client-secret"))
            assertFalse(xml.readText().contains("private-refresh-token"))
            val restored = GoogleDriveAccount(EncryptedSecretStore(context), client)
            assertTrue(restored.signedIn.value)
            assertEquals("test-access", restored.accessToken(false))
            restored.disconnect()
            assertNull(secrets.get(key))
        } finally { secrets.set(key, previous) }
    }

    @Test
    fun uploadSettingsExposeUserCredentialFields() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val app = instrumentation.targetContext.applicationContext as NpiaApplication
        val activity = instrumentation.startActivitySync(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        lateinit var model: AppViewModel
        instrumentation.runOnMainSync { model = ViewModelProvider(activity)[AppViewModel::class.java] }
        val settings = model.manager.settings.value
        try {
            model.manager.updateSettings { it.copy(upload = it.upload.copy(hosts = listOf("gdrive"))) }
            instrumentation.runOnMainSync { model.select(Tab.SETTINGS); model.push(Route.Upload) }
            withTimeout(30_000) {
                while (instrumentation.uiAutomation.rootInActiveWindow == null) delay(200)
            }
            delay(500)
            val labels = mutableSetOf<String>()
            repeat(8) {
                val root = instrumentation.uiAutomation.rootInActiveWindow
                fun visit(node: android.view.accessibility.AccessibilityNodeInfo) {
                    node.text?.toString()?.let(labels::add)
                    for (i in 0 until node.childCount) node.getChild(i)?.let(::visit)
                }
                fun scroll(node: android.view.accessibility.AccessibilityNodeInfo): Boolean {
                    if (node.isScrollable) return node.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
                    for (i in 0 until node.childCount) if (node.getChild(i)?.let(::scroll) == true) return true
                    return false
                }
                if (root != null) { visit(root); scroll(root) }
                delay(150)
            }
            for (label in listOf("OAuth client ID", "Client secret (if required)", "Refresh token", "Sign in to Google")) {
                assertTrue("Missing $label in $labels", labels.any { label in it })
            }
        } finally {
            model.manager.updateSettings { settings }
            instrumentation.runOnMainSync { activity.finish() }
        }
    }
}
