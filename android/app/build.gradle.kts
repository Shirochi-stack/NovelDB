plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.shirochi.npia.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.shirochi.npiamobile"
        minSdk = 26
        targetSdk = 35
        // CI run numbers only grow, so every CI build can update the previous one in place.
        versionCode = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1
        versionName = "0.1.0"
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
}
