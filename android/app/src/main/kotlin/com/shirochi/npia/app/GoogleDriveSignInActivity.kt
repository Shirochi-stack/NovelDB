package com.shirochi.npia.app

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import com.google.android.gms.auth.api.identity.AuthorizationRequest
import com.google.android.gms.auth.api.identity.AuthorizationResult
import com.google.android.gms.auth.api.identity.ClearTokenRequest
import com.google.android.gms.auth.api.identity.Identity
import com.google.android.gms.common.api.Scope
import com.shirochi.npia.upload.UploadException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Google Play services owns the grant and refreshes access without storing OAuth secrets in settings. */
class GoogleDriveAccount(private val context: Context) {
    private val preferences = context.getSharedPreferences("google-drive", Context.MODE_PRIVATE)
    private val state = MutableStateFlow(preferences.getBoolean("authorized", false))
    val signedIn = state.asStateFlow()
    private var cached: Pair<String, Long>? = null
    private var generation = 0L

    fun accept(result: AuthorizationResult) {
        val token = result.accessToken ?: throw UploadException.NotSignedIn("Google Drive")
        synchronized(this) { generation++; cached = token to System.currentTimeMillis() + 45 * 60_000 }
        preferences.edit().putBoolean("authorized", true).apply()
        state.value = true
    }

    fun disconnect() {
        synchronized(this) { generation++; cached = null }
        preferences.edit().remove("authorized").apply()
        state.value = false
    }

    suspend fun accessToken(force: Boolean): String {
        if (!state.value) throw UploadException.NotSignedIn("Google Drive")
        val requestGeneration = synchronized(this) { generation }
        if (!force) synchronized(this) { cached?.takeIf { it.second > System.currentTimeMillis() }?.let { return it.first } }
        val client = Identity.getAuthorizationClient(context)
        val rejectedToken = if (force) synchronized(this) { cached?.first } else null
        if (rejectedToken != null) suspendCancellableCoroutine<Unit> { continuation ->
            client.clearToken(ClearTokenRequest.builder().setToken(rejectedToken).build())
                .addOnSuccessListener { if (continuation.isActive) continuation.resume(Unit) }
                .addOnFailureListener { if (continuation.isActive) continuation.resumeWithException(it) }
        }
        val result = suspendCancellableCoroutine<AuthorizationResult> { continuation ->
            client.authorize(request())
                .addOnSuccessListener { if (continuation.isActive) continuation.resume(it) }
                .addOnFailureListener { if (continuation.isActive) continuation.resumeWithException(it) }
        }
        if (result.hasResolution()) {
            synchronized(this) { if (generation == requestGeneration) disconnect() }
            throw UploadException.NotSignedIn("Google Drive")
        }
        val token = result.accessToken ?: throw UploadException.NotSignedIn("Google Drive")
        synchronized(this) {
            if (!state.value || generation != requestGeneration) throw UploadException.NotSignedIn("Google Drive")
            cached = token to System.currentTimeMillis() + 45 * 60_000
        }
        return token
    }

    companion object {
        fun request(): AuthorizationRequest = AuthorizationRequest.builder()
            .setRequestedScopes(listOf(Scope("https://www.googleapis.com/auth/drive.file"))).build()
    }
}

/** Uses the native consent screen so browser-embedded Google sign-in restrictions do not apply. */
class GoogleDriveSignInActivity : ComponentActivity() {
    private val manager get() = (application as NpiaApplication).downloads
    private val authorization = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { response ->
        if (response.resultCode != RESULT_OK) {
            manager.message("Google sign-in cancelled")
            finish()
            return@registerForActivityResult
        }
        try {
            val result = Identity.getAuthorizationClient(this).getAuthorizationResultFromIntent(response.data)
            complete(result)
        } catch (error: Exception) { fail(error) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState != null) return
        Identity.getAuthorizationClient(this).authorize(GoogleDriveAccount.request())
            .addOnSuccessListener { result ->
                if (result.hasResolution()) authorization.launch(IntentSenderRequest.Builder(result.pendingIntent!!.intentSender).build())
                else complete(result)
            }.addOnFailureListener(::fail)
    }

    private fun complete(result: AuthorizationResult) {
        try {
            manager.googleDrive.accept(result)
            manager.message("Signed in to Google Drive")
            finish()
        } catch (error: Exception) { fail(error) }
    }

    private fun fail(error: Exception) {
        val message = if ((error as? com.google.android.gms.common.api.ApiException)?.statusCode == 10)
            "Google Drive sign-in is unavailable for this build. Please choose another host."
        else "Google Drive sign-in did not finish: ${error.localizedMessage ?: error.javaClass.simpleName}"
        manager.message(message)
        finish()
    }
}
