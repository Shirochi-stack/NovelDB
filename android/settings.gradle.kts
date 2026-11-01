pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "NpiaMobileAndroid"

// Pure JVM modules (unit-tested without a device).
include(":core", ":net", ":output", ":upload", ":sources", ":pipeline")
// Android modules.
include(":webengine", ":images", ":app")
