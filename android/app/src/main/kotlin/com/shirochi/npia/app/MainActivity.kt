package com.shirochi.npia.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.shirochi.npia.app.ui.Accent
import com.shirochi.npia.app.ui.RootScreen
import com.shirochi.npia.app.ui.ThemeMode
import com.shirochi.npia.app.ui.appColorScheme
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val model: AppViewModel by viewModels()

    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (savedInstanceState == null) handle(intent)
        askForNotifications()
        keepScreenAwakeWhileDownloading()
        setContent {
            model.manager.preferencesFlow.collectAsStateWithLifecycle().value
            val preferences = model.manager.preferences
            val colors = appColorScheme(LocalContext.current, Accent.of(preferences.accent), ThemeMode.of(preferences.theme), isSystemInDarkTheme())
            val darkBars = colors.background.luminance() < 0.5f
            DisposableEffect(darkBars) {
                val style = if (darkBars) SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
                else SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
                enableEdgeToEdge(statusBarStyle = style, navigationBarStyle = style)
                onDispose { }
            }
            MaterialTheme(colorScheme = colors) {
                RootScreen(model)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    /** Share-sheet text goes to the Add screen; notification taps open the queue. */
    private fun handle(intent: Intent?) {
        intent ?: return
        val uploadRun = intent.getStringExtra(EXTRA_UPLOAD_RUN)
        if (uploadRun != null) {
            // The summary notification's Upload button, before the sharing agreement was accepted.
            model.select(Tab.QUEUE)
            model.requestUpload {
                if (!model.manager.uploadRun(uploadRun)) model.manager.message("Those downloads are no longer available to upload.")
            }
        } else if (intent.action == Intent.ACTION_SEND) {
            val text = listOfNotNull(intent.getStringExtra(Intent.EXTRA_TEXT), intent.getStringExtra(Intent.EXTRA_SUBJECT)).joinToString("\n")
            if (text.isNotBlank()) {
                model.sharedText = text
                model.select(Tab.ADD)
            }
        } else if (intent.getBooleanExtra(EXTRA_OPEN_QUEUE, false)) {
            model.tab = Tab.QUEUE
        }
    }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val preferences = model.manager.preferences
        if (preferences.askedForNotifications) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        preferences.askedForNotifications = true
        notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun keepScreenAwakeWhileDownloading() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                combine(model.manager.jobs, model.manager.preferencesFlow) { jobs, _ -> jobs.any { it.state.isActive } }
                    .collect { active ->
                        if (active && model.manager.preferences.keepScreenAwake) {
                            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        } else {
                            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        }
                    }
            }
        }
    }

    companion object {
        const val EXTRA_OPEN_QUEUE = "openQueue"
        const val EXTRA_UPLOAD_RUN = "uploadRun"
    }
}
