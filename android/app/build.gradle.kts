plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Release builds made by CI carry a build number (Unix time in seconds, planned by the release
// workflow), the release tag and the label (`none`, `hotfix3`, ...): -PnoveldbBuild=1793193554
// -PnoveldbTag=v98 -PnoveldbLabel=none. The in-app updater compares the build numbers. A build
// without a build number (local, debug, pull request) is a development build and offers no updates.
fun buildProperty(name: String): String? = (findProperty(name) as String?)?.trim()?.takeIf { it.isNotEmpty() }

val noveldbBuild: Long = buildProperty("noveldbBuild")?.toLongOrNull()?.takeIf { it > 0 } ?: 0L
// Only plain tag and label text goes into the generated Java source.
fun displayText(name: String): String =
    if (noveldbBuild > 0) buildProperty(name)?.takeIf { Regex("[A-Za-z0-9._-]{1,40}").matches(it) } ?: "" else ""
val noveldbTag = displayText("noveldbTag")
val noveldbLabel = displayText("noveldbLabel")

android {
    namespace = "com.shirochi.npia.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.shirochi.npiamobile"
        minSdk = 26
        targetSdk = 35
        if (noveldbBuild > 0) {
            // Unix minutes: always larger than the run-number codes of earlier builds, and an Int until the year 6000.
            versionCode = (noveldbBuild / 60).toInt()
            val number = noveldbTag.removePrefix("v").removePrefix("V")
            val label = noveldbLabel.takeUnless { it.isEmpty() || it.equals("none", ignoreCase = true) }
            versionName = when {
                number.isEmpty() -> "0.1.0"
                label == null -> number
                else -> "$number-$label"
            }
        } else {
            // CI run numbers only grow, so every CI build can update the previous one in place.
            versionCode = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1
            versionName = "0.1.0"
        }
        buildConfigField("long", "NOVELDB_BUILD", "${noveldbBuild}L")
        buildConfigField("String", "NOVELDB_TAG", "\"$noveldbTag\"")
        buildConfigField("String", "NOVELDB_LABEL", "\"$noveldbLabel\"")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    // The release key comes from CI secrets (see .github/workflows/android.yml); without it,
    // release builds fall back to the debug key so they still install.
    val releaseKeystore = System.getenv("NPIA_KEYSTORE_PATH")?.let(::file)?.takeIf { it.exists() }
    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = releaseKeystore
                storeType = "pkcs12"
                storePassword = System.getenv("NPIA_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("NPIA_KEY_ALIAS")
                keyPassword = System.getenv("NPIA_KEYSTORE_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources.excludes += setOf("META-INF/AL2.0", "META-INF/LGPL2.1", "META-INF/*.kotlin_module")
    }
}

dependencies {
    implementation(project(":pipeline"))
    implementation(project(":net"))
    implementation(project(":webengine"))
    implementation(project(":images"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.security.crypto)
    implementation(libs.coroutines.android)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.androidx.webkit)
    implementation(libs.compose.material.icons)
    implementation(libs.compose.ui.tooling.preview)
    testImplementation(libs.junit)
    androidTestImplementation(libs.junit)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.coroutines.android)
}
