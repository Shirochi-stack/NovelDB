package com.shirochi.npia.upload

import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URLDecoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * The app's Google OAuth client. Register a client in Google Cloud Console (see
 * `android/docs/upload.md`) and put its id in [BUNDLED_CLIENT_ID], or set [clientIdOverride] at
 * startup (for example from `BuildConfig`). Drive stays hidden while neither is filled in.
 */
class GoogleOAuthConfig private constructor(val clientId: String) {
    /** Google redirects to the reversed client id, e.g. `com.googleusercontent.apps.123-abc`. */
    val callbackScheme: String get() = "com.googleusercontent.apps." + clientId.removeSuffix(CLIENT_SUFFIX)

    val redirectUri: String get() = "$callbackScheme:/oauth2redirect"

    override fun equals(other: Any?): Boolean = other is GoogleOAuthConfig && other.clientId == clientId

    override fun hashCode(): Int = clientId.hashCode()

    override fun toString(): String = "GoogleOAuthConfig($clientId)"

    companion object {
        /** Placeholder: paste the OAuth client ID (`1234-abc.apps.googleusercontent.com`) here. */
        const val BUNDLED_CLIENT_ID = ""

        /** Takes precedence over [BUNDLED_CLIENT_ID] when set. */
        @Volatile
        var clientIdOverride: String? = null

        const val SCOPE = "https://www.googleapis.com/auth/drive.file"
        internal const val CLIENT_SUFFIX = ".apps.googleusercontent.com"
        val AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth".toHttpUrl()
        val TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token".toHttpUrl()
        val REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke".toHttpUrl()

        /** null for an empty or placeholder id. */
        fun of(clientId: String?): GoogleOAuthConfig? {
            val id = clientId.orEmpty().trim()
            if (!id.endsWith(CLIENT_SUFFIX) || id.length <= CLIENT_SUFFIX.length || id.contains("\$(") || id.contains("\${")) return null
            return GoogleOAuthConfig(id)
        }

        fun configured(): GoogleOAuthConfig? = of(clientIdOverride ?: BUNDLED_CLIENT_ID)
    }
}

/** RFC 7636 code verifier and S256 challenge. */
data class Pkce(val verifier: String) {
    val challenge: String = base64Url(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))

    companion object {
        private val random = SecureRandom()

        fun generate(): Pkce = Pkce(randomToken(48))

        internal fun randomToken(bytes: Int): String = base64Url(ByteArray(bytes).also(random::nextBytes))

        internal fun base64Url(data: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(data)
    }
}

/**
 * Opens the consent page and returns the redirect URI Google sent back. The app implements it
 * with a Custom Tab plus an activity that catches [callbackScheme]; tests stub it. Throw
 * `CancellationException` when the user closes the page.
 */
interface OAuthBrowser {
    suspend fun authenticate(url: String, callbackScheme: String): String
}

@Serializable
internal data class GoogleTokens(
    @SerialName("access_token") val accessToken: String,
    @SerialName("expires_in") val expiresIn: Double? = null,
    @SerialName("refresh_token") val refreshToken: String? = null,
)

/**
 * Google sign-in with PKCE (no Google SDK, no client secret). Only the refresh token is stored, in
 * `SecretStore` under `SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN`; access tokens stay in memory.
 */
class GoogleDriveAuth(
    val config: GoogleOAuthConfig,
    private val secrets: SecretStore,
    private val client: OkHttpClient = UploadContext.defaultClient,
    private val now: () -> Long = System::currentTimeMillis,
) {
    private val lock = Any()
    private var cached: Pair<String, Long>? = null

    val isSignedIn: Boolean get() = !secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN).isNullOrEmpty()

    /** Runs the consent flow and saves the refresh token. */
    suspend fun signIn(browser: OAuthBrowser) {
        val pkce = Pkce.generate()
        val state = Pkce.randomToken(16)
        val callback = browser.authenticate(authorizationUrl(config, pkce, state).toString(), config.callbackScheme)
        val code = code(callback, state)
        val tokens = requestTokens(exchangeRequest(config, code, pkce))
        if (tokens.refreshToken.isNullOrEmpty()) throw UploadException.Rejected(HOST, "no refresh token was issued")
        store(tokens)
    }

    /** Returns a cached access token, refreshing it when it is about to expire. */
    suspend fun accessToken(forceRefresh: Boolean = false): String {
        if (!forceRefresh) validCachedToken()?.let { return it }
        val refresh = secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN)
        if (refresh.isNullOrEmpty()) throw UploadException.NotSignedIn(HOST)
        val tokens = requestTokens(refreshRequest(config, refresh))
        store(tokens)
        return tokens.accessToken
    }

    /** Revokes the grant (best effort) and forgets the tokens. */
    suspend fun signOut() {
        val refresh = secrets.get(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN)
        if (!refresh.isNullOrEmpty()) {
            try {
                client.send(formRequest(GoogleOAuthConfig.REVOKE_ENDPOINT, listOf("token" to refresh)))
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
        }
        secrets.set(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN, null)
        synchronized(lock) { cached = null }
    }

    private suspend fun requestTokens(request: Request): GoogleTokens {
        val reply = client.send(request)
        if (!reply.isSuccess) {
            val error = reply.jsonObject()?.string("error")
            if (error == "invalid_grant") {
                secrets.set(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN, null)
                throw UploadException.NotSignedIn(HOST)
            }
            throw UploadException.BadResponse(HOST, reply.status, error ?: reply.snippet)
        }
        return try {
            json.decodeFromString(GoogleTokens.serializer(), reply.body.toString(Charsets.UTF_8))
        } catch (e: Exception) {
            throw UploadException.Rejected(HOST, "unreadable token reply: ${reply.snippet}")
        }
    }

    private fun store(tokens: GoogleTokens) {
        synchronized(lock) {
            val lifetime = ((tokens.expiresIn ?: 3_600.0) - 60).toLong() * 1000
            cached = tokens.accessToken to now() + lifetime
            if (!tokens.refreshToken.isNullOrEmpty()) secrets.set(SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN, tokens.refreshToken)
        }
    }

    private fun validCachedToken(): String? = synchronized(lock) {
        cached?.takeIf { it.second > now() }?.first
    }

    companion object {
        internal const val HOST = "Google Drive"
        private val json = Json { ignoreUnknownKeys = true }

        fun authorizationUrl(config: GoogleOAuthConfig, pkce: Pkce, state: String): HttpUrl =
            GoogleOAuthConfig.AUTHORIZE_ENDPOINT.newBuilder()
                .addQueryParameter("client_id", config.clientId)
                .addQueryParameter("redirect_uri", config.redirectUri)
                .addQueryParameter("response_type", "code")
                .addQueryParameter("scope", GoogleOAuthConfig.SCOPE)
                .addQueryParameter("code_challenge", pkce.challenge)
                .addQueryParameter("code_challenge_method", "S256")
                .addQueryParameter("state", state)
                .build()

        /** Query items of a redirect URI such as `com.googleusercontent.apps.x:/oauth2redirect?code=...`. */
        internal fun queryOf(uri: String): Map<String, String> {
            val query = uri.substringAfter('?', "").substringBefore('#')
            if (query.isEmpty()) return emptyMap()
            return query.split('&').filter { it.isNotEmpty() }.associate { pair ->
                val key = pair.substringBefore('=')
                val value = pair.substringAfter('=', "")
                URLDecoder.decode(key, "UTF-8") to URLDecoder.decode(value, "UTF-8")
            }
        }

        /** Pulls the authorization code out of the redirect, checking `state`. */
        fun code(callback: String, state: String): String {
            val items = queryOf(callback)
            items["error"]?.let { error ->
                if (error == "access_denied") throw CancellationException("Google sign-in was cancelled")
                throw UploadException.Rejected(HOST, error)
            }
            if (items["state"] != state) throw UploadException.Rejected(HOST, "sign-in state mismatch")
            val code = items["code"]
            if (code.isNullOrEmpty()) throw UploadException.Rejected(HOST, "no authorization code")
            return code
        }

        internal fun formRequest(url: HttpUrl, pairs: List<Pair<String, String>>): Request =
            Request.Builder()
                .url(url)
                .post(formEncoded(pairs).toByteArray(Charsets.UTF_8).toRequestBody("application/x-www-form-urlencoded".toMediaType()))
                .build()

        internal fun exchangeRequest(config: GoogleOAuthConfig, code: String, pkce: Pkce): Request =
            formRequest(
                GoogleOAuthConfig.TOKEN_ENDPOINT,
                listOf(
                    "client_id" to config.clientId,
                    "code" to code,
                    "code_verifier" to pkce.verifier,
                    "grant_type" to "authorization_code",
                    "redirect_uri" to config.redirectUri,
                ),
            )

        internal fun refreshRequest(config: GoogleOAuthConfig, refreshToken: String): Request =
            formRequest(
                GoogleOAuthConfig.TOKEN_ENDPOINT,
                listOf(
                    "client_id" to config.clientId,
                    "grant_type" to "refresh_token",
                    "refresh_token" to refreshToken,
                ),
            )
    }
}
