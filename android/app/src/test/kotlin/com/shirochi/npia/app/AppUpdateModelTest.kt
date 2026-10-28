package com.shirochi.npia.app

import android.content.pm.PackageInstaller
import com.shirochi.npia.app.model.ConfigTransfer
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.InMemorySecretStore
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The parts of the in-app updater that need no device. The rules themselves are tested in `:core` and `:net`. */
class AppUpdateModelTest {
    @Test
    fun aSigningKeyMismatchTellsTheUserToUninstallFirst() {
        val android = "INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package com.shirochi.npiamobile signatures do not match newer version; ignoring!"
        val text = InstallOutcome.failure(PackageInstaller.STATUS_FAILURE_INCOMPATIBLE, android)
        assertEquals(InstallOutcome.SIGNING_KEY, text)
        assertTrue(text.contains("Uninstall the old NovelDB first"))
        assertTrue(text.contains("your settings will be lost"))
        // Newer Android releases word it differently.
        assertEquals(
            InstallOutcome.SIGNING_KEY,
            InstallOutcome.failure(PackageInstaller.STATUS_FAILURE_INCOMPATIBLE, "Package com.x signatures do not match previously installed version; ignoring!"),
        )
    }

    @Test
    fun otherFailuresAreDescribedWithoutTheUninstallAdvice() {
        assertEquals("The install was cancelled.", InstallOutcome.failure(PackageInstaller.STATUS_FAILURE_ABORTED, "INSTALL_FAILED_ABORTED: User rejected permissions"))
        assertTrue(InstallOutcome.failure(PackageInstaller.STATUS_FAILURE_STORAGE, null).contains("storage"))
        assertEquals(
            "Android refused the update because the installed NovelDB is newer.",
            InstallOutcome.failure(PackageInstaller.STATUS_FAILURE_INVALID, "INSTALL_FAILED_VERSION_DOWNGRADE"),
        )
        val generic = InstallOutcome.failure(PackageInstaller.STATUS_FAILURE, "Something odd")
        assertEquals("The install failed: Something odd", generic)
        assertFalse(generic.contains("Uninstall"))
        assertEquals("The install failed (status ${PackageInstaller.STATUS_FAILURE}).", InstallOutcome.failure(PackageInstaller.STATUS_FAILURE, null))
        assertTrue(InstallOutcome.failure(PackageInstaller.STATUS_FAILURE_BLOCKED, null).contains("allowed"))
    }

    @Test
    fun theUpdateSettingMovesBetweenTheDesktopAndAppConfigs() {
        val secrets = InMemorySecretStore()
        // A desktop file that turns it off.
        val imported = ConfigTransfer.import("""{"app_update_check": false, "custom_unknown": 5}""", AppSettings(), secrets) as ConfigTransfer.Imported.Desktop
        assertFalse(imported.settings.appUpdateCheck)

        val exported = Json.parseToJsonElement(ConfigTransfer.exportDesktop(imported.settings, imported.base)).jsonObject
        assertEquals(false, exported.getValue("app_update_check").jsonPrimitive.boolean)
        assertEquals(5, exported.getValue("custom_unknown").jsonPrimitive.content.toInt())

        // The app's own settings file carries it as well.
        val appFile = ConfigTransfer.import(ConfigTransfer.exportApp(imported.settings), AppSettings(), secrets) as ConfigTransfer.Imported.AppFile
        assertFalse(appFile.settings.appUpdateCheck)

        // Importing a desktop file that does not mention it leaves the current choice; the default is on.
        assertTrue((ConfigTransfer.import("""{"ext_threads": 3}""", AppSettings(), secrets) as ConfigTransfer.Imported.Desktop).settings.appUpdateCheck)
        assertFalse((ConfigTransfer.import("""{"ext_threads": 3}""", imported.settings, secrets) as ConfigTransfer.Imported.Desktop).settings.appUpdateCheck)
        // An app file from before the setting existed turns it on.
        val old = ConfigTransfer.import("""{"schemaVersion": 1, "shared": {"threads": 2}}""", imported.settings, secrets) as ConfigTransfer.Imported.AppFile
        assertTrue(old.settings.appUpdateCheck)
    }
}
