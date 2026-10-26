plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    api(project(":core"))
    api(project(":output"))
    api(project(":upload"))
    api(project(":sources"))
    implementation(project(":net"))
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
}
