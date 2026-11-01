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

// Android gives the app 256 MB; DownloadPipelineMemoryTest proves a larger book of pictures fits in that.
tasks.test {
    maxHeapSize = "256m"
}
