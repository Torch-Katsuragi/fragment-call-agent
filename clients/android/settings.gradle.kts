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
        // livekit-android が依存する AudioSwitch (フォーク) は JitPack にしか無い。
        //   ⚠そのグループだけに絞る — 他の依存が JitPack から解決されないように
        maven("https://jitpack.io") {
            content { includeGroup("com.github.davidliu") }
        }
    }
}

// ⚠バージョンはローカルのGradleキャッシュに既にあるものへ揃えてある (オフラインでも解決できる)
plugins {
    id("com.android.application") version "8.9.1" apply false
    id("org.jetbrains.kotlin.android") version "2.1.0" apply false
    // Kotlin 2.0以降、Composeコンパイラは独立プラグイン。Kotlin本体とバージョンを揃える
    id("org.jetbrains.kotlin.plugin.compose") version "2.1.0" apply false
}

rootProject.name = "fragment-phone"
include(":app")
