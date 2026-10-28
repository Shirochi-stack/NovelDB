package com.shirochi.npia.app.ui

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.sources.session.HardSessions
import com.shirochi.npia.sources.session.SessionSite

/**
 * The desktop's "Enter App": one place to sign in to the sources whose chapters need the user's own
 * account. sfc signs in through its app API (or takes a pasted app cookie); qdn and rbooks sign in
 * on their websites, in the app's own browser, and downloads share that browser's cookies.
 */
@Composable
fun EnterAppScreen(model: AppViewModel) {
    val manager = model.manager
    // Re-read the sessions whenever a sign-in or sign-out ends.
    val version by manager.sessionVersion.collectAsStateWithLifecycle()
    val sfcWorking by manager.sfcWorking.collectAsStateWithLifecycle()
    val sfcSignedIn = remember(version) { manager.sfcSignedIn() }

    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var cookie by remember { mutableStateOf("") }

    SettingsColumn {
        SectionHeader("sfc")
        Footnote(if (sfcSignedIn) "App session saved: VIP chapters you bought download through the app API." else "No app session saved. Only free chapters can be read.")
        OutlinedTextField(
            value = username, onValueChange = { username = it }, label = { Text("Username") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )
        OutlinedTextField(
            value = password, onValueChange = { password = it }, label = { Text("Password") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )
        Row(Modifier.padding(start = 8.dp)) {
            TextButton(
                enabled = !sfcWorking && username.isNotBlank() && password.isNotEmpty(),
                onClick = {
                    manager.signInToSfc(username.trim(), password)
                    password = ""
                },
            ) { Text(if (sfcWorking) "Signing in…" else "Sign in") }
        }
        OutlinedTextField(
            value = cookie, onValueChange = { cookie = it }, label = { Text("App cookie") },
            placeholder = { Text(".SFCommunity=...; session_APP=...") }, minLines = 2, maxLines = 4,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )
        Footnote("Or paste the cookie header from the sfc app. It must contain session_APP.")
        Row(Modifier.padding(start = 8.dp)) {
            TextButton(enabled = cookie.isNotBlank(), onClick = { if (manager.saveSfcCookie(cookie)) cookie = "" }) { Text("Save cookie") }
            if (sfcSignedIn) {
                TextButton(onClick = { manager.signOutOfSfc() }) { Text("Sign out", color = MaterialTheme.colorScheme.error) }
            }
        }
        Footnote("Stored only in encrypted app storage, never in settings files or exports.")

        BrowserSession(model, "qdn", HardSessions.qdn, version,
            "Opens your qdn account in the app's browser, which asks you to sign in. Sign in there, then tap Done; if the site shows " +
                "a verification, complete it first. qdn keeps this login only for the browser session, so the app also saves a copy " +
                "and puts it back if the browser loses it.")
        BrowserSession(model, "rbooks", HardSessions.rbooks, version,
            "Opens the rbooks sign-in page in the app's browser. Sign in there (and finish adult verification if asked), then tap Done.")
        Footnote("Downloads use the same browser, so a login here is a login for downloads. Chapters your account has not bought are skipped; nothing is ever purchased.")
    }
}

@Composable
private fun BrowserSession(model: AppViewModel, label: String, site: SessionSite, version: Int, help: String) {
    val manager = model.manager
    val status = remember(version) { manager.siteStatus(site) }
    SectionHeader(label)
    Footnote(
        when {
            status.usable -> "Signed in (found ${status.present.sorted().joinToString(", ")})."
            status.present.isNotEmpty() -> "Partly signed in: found ${status.present.sorted().joinToString(", ")}, but ${site.requirement} are needed."
            status.hasSavedCopy -> "Not signed in. A saved copy exists and is restored when a download starts."
            else -> "Not signed in. Free chapters still download."
        },
    )
    Footnote(help)
    Row(Modifier.padding(start = 8.dp)) {
        TextButton(onClick = { manager.signInToSite(site) }) { Text(if (status.usable) "Sign in again" else "Sign in") }
        if (status.present.isNotEmpty() || status.hasSavedCopy) {
            TextButton(onClick = { manager.signOutOfSite(site) }) { Text("Sign out", color = MaterialTheme.colorScheme.error) }
        }
    }
}
