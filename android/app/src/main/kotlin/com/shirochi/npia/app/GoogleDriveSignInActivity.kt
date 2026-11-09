package com.shirochi.npia.app

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import com.google.android.gms.auth.api.identity.AuthorizationRequest
import com.google.android.gms.auth.api.identity.AuthorizationResult
import com.google.android.gms.auth.api.identity.ClearTokenRequest
import com.google.android.gms.auth.api.identity.Identity
import com.google.android.gms.common.api.Scope
import com.google.android.gms.common.api.ApiException
import com.google.android.gms.common.api.CommonStatusCodes
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
    private val error = MutableStateFlow<String?>(null)
    val signInError = error.asStateFlow()
    private var cached: Pair<String, Long>? = null
    private var generation = 0L

    fun accept(result: AuthorizationResult) {
        val token = result.accessToken ?: throw UploadException.NotSignedIn("Google Drive")
        synchronized(this) { generation++; cached = token to System.currentTimeMillis() + 45 * 60_000 }
        preferences.edit().putBoolean("authorized", true).apply()
        error.value = null
        state.value = true
    }

    fun disconnect() {
        synchronized(this) { generation++; cached = null }
        preferences.edit().remove("authorized").apply()
        error.value = null
        state.value = false
    }

    fun signInFailed(message: String) { error.value = message }

    fun clearSignInError() { error.value = null }

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

/** Google carries success and failure details in the intent, including non-OK activity results. */
internal fun googleDriveAuthorizationResult(context: Context, response: ActivityResult): AuthorizationResult {
    @Suppress("DEPRECATION")
    val launchError = response.data?.getSerializableExtra(ActivityResultContracts.StartIntentSenderForResult.EXTRA_SEND_INTENT_EXCEPTION) as? Exception
    if (launchError != null) throw launchError
    return Identity.getAuthorizationClient(context).getAuthorizationResultFromIntent(response.data)
}

internal fun googleDriveSignInFailure(error: Exception): String {
    val api = error as? ApiException
    val detail = api?.status?.statusMessage?.takeIf { it.isNotBlank() }
        ?: api?.let { CommonStatusCodes.getStatusCodeString(it.statusCode) }
        ?: error.localizedMessage?.takeIf { it.isNotBlank() }
        ?: error.javaClass.simpleName
    return when (api?.statusCode) {
        CommonStatusCodes.DEVELOPER_ERROR ->
            "Google Drive authorization needs publisher setup (Google error ${api.statusCode}). " +
                "Register this app's package and signing certificate in Google Cloud. $detail"
        else -> "Google Drive authorization did not complete" +
            (api?.let { " (Google error ${it.statusCode})" } ?: "") + ". $detail"
    }
}

/** Uses the native consent screen so browser-embedded Google sign-in restrictions do not apply. */
class GoogleDriveSignInActivity : ComponentActivity() {
    private val manager get() = (application as NpiaApplication).downloads
    private val authorization = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { response ->
        try {
            complete(googleDriveAuthorizationResult(this, response))
        } catch (error: Exception) { fail(error) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState != null) return
        manager.googleDrive.clearSignInError()
        Identity.getAuthorizationClient(this).authorize(GoogleDriveAccount.request())
            .addOnSuccessListener { result ->
                try {
                    if (result.hasResolution()) authorization.launch(IntentSenderRequest.Builder(result.pendingIntent!!.intentSender).build())
                    else complete(result)
                } catch (error: Exception) { fail(error) }
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
        val message = googleDriveSignInFailure(error)
        manager.googleDrive.signInFailed(message)
        manager.message(message)
        finish()
    }
}
