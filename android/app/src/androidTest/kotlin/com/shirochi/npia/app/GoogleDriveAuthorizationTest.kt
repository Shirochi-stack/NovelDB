package com.shirochi.npia.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.IntentSender
import android.os.Bundle
import androidx.activity.result.ActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.android.gms.auth.api.identity.AuthorizationResult
import com.google.android.gms.common.api.ApiException
import com.google.android.gms.common.api.CommonStatusCodes
import com.google.android.gms.common.api.Status
import com.google.android.gms.common.internal.safeparcel.SafeParcelableSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Exercises the real Google SDK's result parser without requesting a live account grant. */
@RunWith(AndroidJUnit4::class)
class GoogleDriveAuthorizationTest {
    private val context get() = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun nonOkActivityResultStillReadsAReturnedAuthorization() {
        val expected = AuthorizationResult(null, "test-access-token", null,
            listOf("https://www.googleapis.com/auth/drive.file"), null, null, Bundle.EMPTY)
        val data = Intent()
        SafeParcelableSerializer.serializeToIntentExtra(Status.RESULT_SUCCESS, data, "status")
        SafeParcelableSerializer.serializeToIntentExtra(expected, data, "authorization_result")
        val result = googleDriveAuthorizationResult(context, ActivityResult(Activity.RESULT_CANCELED, data))
        assertEquals(expected.accessToken, result.accessToken)
        assertEquals(expected.grantedScopes, result.grantedScopes)
    }

    @Test
    fun canceledActivityPreservesGooglesFailureStatus() {
        for ((code, reason) in listOf(CommonStatusCodes.DEVELOPER_ERROR to "OAuth client is not configured",
            CommonStatusCodes.CANCELED to "Account reauth failed", CommonStatusCodes.NETWORK_ERROR to "Network unavailable")) {
            val data = Intent()
            SafeParcelableSerializer.serializeToIntentExtra(Status(code, reason), data, "status")
            val error = runCatching { googleDriveAuthorizationResult(context, ActivityResult(Activity.RESULT_CANCELED, data)) }.exceptionOrNull()
            assertTrue(error is ApiException)
            assertEquals(code, (error as ApiException).statusCode)
            val message = googleDriveSignInFailure(error)
            assertTrue(message, message.contains("Google error $code") && message.contains(reason))
            assertFalse(message == "Google sign-in cancelled")
            if (code == CommonStatusCodes.DEVELOPER_ERROR) assertTrue(message.contains("publisher setup"))
        }
    }

    @Test
    fun intentSenderFailureKeepsItsCause() {
        val data = Intent().putExtra(ActivityResultContracts.StartIntentSenderForResult.EXTRA_SEND_INTENT_EXCEPTION,
            IntentSender.SendIntentException("Consent screen could not open"))
        val error = runCatching { googleDriveAuthorizationResult(context, ActivityResult(Activity.RESULT_CANCELED, data)) }.exceptionOrNull()
        assertTrue(error is IntentSender.SendIntentException)
        assertTrue(googleDriveSignInFailure(error as Exception).contains("Consent screen could not open"))
    }

    @Test
    fun absentResultDoesNotInventAUserCancellation() {
        val error = runCatching { googleDriveAuthorizationResult(context, ActivityResult(Activity.RESULT_CANCELED, null)) }.exceptionOrNull()
        assertTrue(error is ApiException)
        assertEquals(CommonStatusCodes.INTERNAL_ERROR, (error as ApiException).statusCode)
        assertTrue(googleDriveSignInFailure(error).contains("Google error 8"))
    }
}
