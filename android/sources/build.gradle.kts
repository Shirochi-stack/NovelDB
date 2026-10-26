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
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
}
