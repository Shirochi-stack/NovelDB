package com.shirochi.npia.app

import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.upload.GoogleDriveAuth
import com.shirochi.npia.upload.GoogleOAuthConfig
import com.shirochi.npia.upload.UploadContext
import com.shirochi.npia.upload.UploadException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient

@Serializable
data class GoogleDriveCredentials(val clientId: String = "", val clientSecret: String = "", val refreshToken: String = "") {
    override fun toString() = "GoogleDriveCredentials(redacted)"
}

/** Each user supplies an existing OAuth grant. One encrypted record holds the complete connection. */
class GoogleDriveAccount(private val secrets: SecretStore, private val client: OkHttpClient = UploadContext.defaultClient) {
    private val json = Json { ignoreUnknownKeys = true }
    private val credentialKey = SecretKey("upload.gdrive.credentials")
    private val stored = MutableStateFlow(runCatching {
        json.decodeFromString<GoogleDriveCredentials>(secrets.get(credentialKey).orEmpty())
    }.getOrDefault(GoogleDriveCredentials()))
    val credentials = stored.asStateFlow()
    private var grant = createGrant(stored.value)
    private val state = MutableStateFlow(grant != null)
    val signedIn = state.asStateFlow()
    private val error = MutableStateFlow<String?>(null)
    val signInError = error.asStateFlow()
    private val busy = MutableStateFlow(false)
    val connecting = busy.asStateFlow()
    private val requests = Mutex()
    private var generation = 0L

    private class Grant(val credentials: GoogleDriveCredentials, val tokens: InMemorySecretStore, val auth: GoogleDriveAuth)

    private fun createGrant(value: GoogleDriveCredentials): Grant? {
        val config = GoogleOAuthConfig.of(value.clientId, value.clientSecret) ?: return null
        if (value.refreshToken.isBlank()) return null
        val tokens = InMemorySecretStore(mapOf(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN to value.refreshToken))
        return Grant(value, tokens, GoogleDriveAuth(config, tokens, client))
    }

    suspend fun connect(value: GoogleDriveCredentials) {
        val current = synchronized(this) { ++generation }
        requests.withLock {
            synchronized(this) {
                if (generation != current) throw CancellationException("Google Drive connection was cancelled")
            }
            busy.value = true
            error.value = null
            try {
                val normalized = value.copy(clientId = value.clientId.trim(), clientSecret = value.clientSecret.trim(), refreshToken = value.refreshToken.trim())
                val candidate = createGrant(normalized)
                    ?: throw IllegalArgumentException("Enter your OAuth client ID and refresh token. Include the client secret if your OAuth client requires one.")
                candidate.auth.accessToken()
                synchronized(this) {
                    if (generation != current) throw CancellationException("Google Drive connection was cancelled")
                    persist(candidate)
                    grant = candidate
                    state.value = true
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Exception) {
                synchronized(this) { if (generation == current) error.value = failureMessage(failure) }
                throw failure
            } finally {
                busy.value = false
            }
        }
    }

    fun disconnect() = synchronized(this) {
        generation++
        secrets.set(credentialKey, null)
        grant = null
        stored.value = GoogleDriveCredentials()
        state.value = false
        error.value = null
    }

    suspend fun accessToken(force: Boolean): String = requests.withLock {
        val (current, active) = synchronized(this) {
            generation to (grant ?: throw UploadException.NotSignedIn("Google Drive"))
        }
        val token = try {
            active.auth.accessToken(force)
        } catch (failure: UploadException.NotSignedIn) {
            synchronized(this) {
                if (generation == current) {
                    disconnect()
                    error.value = failureMessage(failure)
                }
            }
            throw failure
        }
        synchronized(this) {
            if (generation != current) throw UploadException.NotSignedIn("Google Drive")
            persist(active)
        }
        token
    }

    private fun persist(active: Grant) {
        val value = active.credentials.copy(refreshToken = active.tokens.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN).orEmpty())
        if (value != stored.value || secrets.get(credentialKey) == null) {
            secrets.set(credentialKey, json.encodeToString(GoogleDriveCredentials.serializer(), value))
            stored.value = value
        }
    }

    private fun failureMessage(failure: Exception): String = when (failure) {
        is UploadException.NotSignedIn -> "The Google refresh token is invalid or expired. Authorize your OAuth client again and enter its new refresh token."
        else -> "Google Drive connection failed. ${failure.localizedMessage ?: failure.javaClass.simpleName}"
    }
}
