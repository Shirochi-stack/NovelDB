package com.shirochi.npia.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.BatteryOptimization
import com.shirochi.npia.app.model.BackgroundTestMeter

/**
 * Asks, in a pop-up, to turn off battery optimization for the app: once after install or update,
 * and again at a start that found downloads the system had stopped. "Allow" opens Android's own
 * question. Nothing shows once optimization is off.
 */
@Composable
fun BackgroundPrompt(model: AppViewModel) {
    val context = LocalContext.current
    val manager = model.manager
    val interrupted = manager.interruptedAtStart
    var answered by rememberSaveable { mutableStateOf(false) }
    val wanted = remember { !BatteryOptimization.off(context) && (!manager.preferences.askedForBatteryOptimization || interrupted > 0) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (BatteryOptimization.off(context)) manager.message("NovelDB can now keep downloading in the background.")
    }
    if (!wanted || answered) return
    fun close() {
        manager.preferences.askedForBatteryOptimization = true
        answered = true
    }
    AlertDialog(
        onDismissRequest = ::close,
        title = { Text("Keep downloads running in the background") },
        text = {
            Text(
                (if (interrupted > 0) "NovelDB was stopped while $interrupted ${if (interrupted == 1) "download was" else "downloads were"} running. " else "") +
                    "Android's battery optimization can stop NovelDB when you leave the app or lock the screen, which interrupts downloads. " +
                    "Allow NovelDB to run in the background so they finish. You can test it in Settings, Screen and background.",
            )
        },
        confirmButton = {
            TextButton(onClick = {
                close()
                if (!BatteryOptimization.open(context, launcher::launch)) manager.message("Could not open the battery settings.")
            }) { Text("Allow") }
        },
        dismissButton = { TextButton(onClick = ::close) { Text("Not now") } },
    )
}

/** Settings, Screen and background: the screen switch, the battery optimization state, and the background test. */
@Composable
fun BackgroundSettingsScreen(model: AppViewModel) {
    val context = LocalContext.current
    val manager = model.manager
    val test = manager.backgroundTest
    manager.preferencesFlow.collectAsStateWithLifecycle().value
    var unlimited by remember { mutableStateOf(BatteryOptimization.off(context)) }
    var restricted by remember { mutableStateOf(BatteryOptimization.restricted(context)) }
    // Read again when the user comes back from the system's question or settings page.
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        unlimited = BatteryOptimization.off(context)
        restricted = BatteryOptimization.restricted(context)
    }
    val progress by test.progress.collectAsStateWithLifecycle()
    val result by test.result.collectAsStateWithLifecycle()

    SettingsColumn {
        SectionHeader("Screen")
        SwitchRow("Keep screen awake while downloading", manager.preferences.keepScreenAwake, { manager.setKeepScreenAwake(it) })
        Footnote("Applies while the app is open.")

        SectionHeader("Battery optimization")
        Text(
            when {
                restricted -> "Restricted: Android stops NovelDB as soon as it is off screen. Set its battery use to Unrestricted."
                unlimited -> "Off for NovelDB: Android lets it keep downloading in the background."
                else -> "On for NovelDB: Android may stop downloads when you leave the app or lock the screen."
            },
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
            color = if (unlimited && !restricted) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error,
        )
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp)) {
            if (!unlimited) {
                TextButton(onClick = {
                    if (!BatteryOptimization.open(context, launcher::launch)) manager.message("Could not open the battery settings.")
                }) { Text("Turn off battery optimization") }
            }
            TextButton(onClick = {
                runCatching { launcher.launch(BatteryOptimization.appSettings(context)) }
                    .onFailure { manager.message("Could not open the app's settings.") }
            }) { Text("App settings") }
        }
        Footnote(
            "Downloads run in a foreground service with a progress notification after you leave the app or lock the screen. " +
                "Some phones have a battery saver of their own (sleeping apps, auto-start, app launch); if the test below fails with " +
                "optimization off, take NovelDB off those lists in the phone's battery settings.",
        )

        SectionHeader("Background test")
        val running = progress
        if (running != null) {
            Text(
                "Running: ${BackgroundTestMeter.span(running.elapsedMs)} of ${BackgroundTestMeter.span(running.totalMs)}. " +
                    "Leave the app or lock the screen now; a notification shows the result.",
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
            )
            LinearProgressIndicator(
                progress = { (running.elapsedMs.toFloat() / running.totalMs.coerceAtLeast(1)).coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
            )
            Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp)) {
                TextButton(onClick = { test.stop() }) { Text("Stop test") }
            }
        } else {
            Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp)) {
                TextButton(onClick = { test.start(2 * 60_000L) }) { Text("Test for 2 minutes") }
                TextButton(onClick = { test.start(15 * 60_000L) }) { Text("Test for 15 minutes") }
            }
        }
        result?.let { last ->
            Text(
                (if (running != null) "Last test: " else "") + last.text,
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
                color = if (last.passed) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            )
        }
        Footnote(
            "Runs NovelDB as a download would, without downloading: a clock ticks every second and the internet is checked every 15 seconds. " +
                "Start it, then leave the app or lock the screen until the notification arrives. " +
                "Some phones stop apps only after a longer time; the 15-minute test finds those.",
        )
    }
}
