plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    api(project(":core"))
    implementation(libs.jsoup)
    // QuickJS for the generic reader (rules-lib.js); bundles JNI libraries for the JVM and Android ABIs.
    implementation(libs.quickjs)
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
}
