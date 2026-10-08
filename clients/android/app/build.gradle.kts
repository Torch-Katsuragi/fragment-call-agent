plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "jp.sleeptree.fragment"
    compileSdk = 35

    defaultConfig {
        applicationId = "jp.sleeptree.fragment"
        // ⚠minSdk 29 = Android 10。ユーザーのメイン端末がAndroid 10のため。
        //   self-managed ConnectionService は API 26+ だが、
        //   FOREGROUND_SERVICE_TYPE_PHONE_CALL が入ったのが 29 なのでここを下限にする
        minSdk = 29
        targetSdk = 35
        versionCode = 2
        versionName = "0.2.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
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
        // AGP 8以降は既定でoff。デモ着信の分岐 (BuildConfig.DEBUG) に要る
        buildConfig = true
    }
}

// ⚠依存は意図的に絞ってある。HTTPは HttpURLConnection、JSONは org.json (どちらもAndroid同梱)。
dependencies {
    // 通話音声 (2026-09-24 に WebView から移した)。受話口/スピーカー/Bluetooth の切り替えと
    //   音量ボタンを OS の通話と同じ扱いにするため (AudioSwitch 同梱)。経緯は audio/CallAudio.kt
    implementation("io.livekit:livekit-android:2.18.2")

    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // FCM (2026-09-18)。⚠google-services プラグインは入れない — 接続情報は管制室が配り、
    //   FirebaseOptions で手で初期化する (push/PushSetup.kt)。self-host ごとに Firebase が違うため
    implementation("com.google.firebase:firebase-messaging:24.1.0")

    // Google アカウントでのログイン (2026-09-26、招待から参加・メンバーのログイン)。GoogleLogin.kt
    implementation("androidx.credentials:credentials:1.5.0")
    implementation("androidx.credentials:credentials-play-services-auth:1.5.0")
    implementation("com.google.android.libraries.identity.googleid:googleid:1.1.1")
}
