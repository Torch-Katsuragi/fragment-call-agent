package jp.sleeptree.fragment.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// 仮の緑。⚠正式なブランドカラーはロゴが決まってから合わせる
private val Green = Color(0xFF1D9E75)
private val GreenDark = Color(0xFF0F6E56)
private val GreenLight = Color(0xFF9FE1CB)

/**
 * 画面の形 (2026-09-24 ネイティブ化で決めた。ユーザー「めざせ good design」)。
 *
 * - 地は薄いグレー、中身は白いカード (角丸 20)。影は付けず、面の明るさの差だけで分ける
 * - 色は意味にだけ使う: 緑 = 自分/操作できる、琥珀 = 相手、赤 = 切る。装飾に色を使わない
 * - 文字は 3 段だけ (見出し / 本文 / 補足)。補足は onSurfaceVariant
 * - アニメーションは付けない (作業用の画面は即時に。音の波形だけは機能なので動かす)
 */
private val LightColors = lightColorScheme(
    primary = Green,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD5F0E5),
    onPrimaryContainer = Color(0xFF04342C),
    secondaryContainer = Color(0xFFE3EFEA),
    onSecondaryContainer = Color(0xFF1E4136),
    tertiaryContainer = Color(0xFFE8E6F6),
    onTertiaryContainer = Color(0xFF2E2A52),
    errorContainer = Color(0xFFFBE3E1),
    onErrorContainer = Color(0xFF6B1512),
    background = Color(0xFFF3F5F3),
    onBackground = Color(0xFF17201C),
    surface = Color(0xFFF3F5F3),
    onSurface = Color(0xFF17201C),
    onSurfaceVariant = Color(0xFF5E6863),
    surfaceVariant = Color(0xFFE7EBE8),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFFAFBFA),
    surfaceContainer = Color(0xFFEDF0EE),
    surfaceContainerHigh = Color(0xFFE7EBE8),
    surfaceContainerHighest = Color(0xFFE1E6E3),
    outline = Color(0xFFB9C2BD),
    outlineVariant = Color(0xFFE2E7E4),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF5CCBA3),
    onPrimary = Color(0xFF00382A),
    primaryContainer = GreenDark,
    onPrimaryContainer = GreenLight,
    secondaryContainer = Color(0xFF203A31),
    onSecondaryContainer = Color(0xFFCDE9DD),
    tertiaryContainer = Color(0xFF2F2C4A),
    onTertiaryContainer = Color(0xFFE2DFFF),
    errorContainer = Color(0xFF5C1D1A),
    onErrorContainer = Color(0xFFFFDAD6),
    background = Color(0xFF0E1311),
    onBackground = Color(0xFFE2E8E4),
    surface = Color(0xFF0E1311),
    onSurface = Color(0xFFE2E8E4),
    onSurfaceVariant = Color(0xFF9AA6A0),
    surfaceVariant = Color(0xFF232B27),
    surfaceContainerLowest = Color(0xFF171D1A),
    surfaceContainerLow = Color(0xFF1A201D),
    surfaceContainer = Color(0xFF1E2522),
    surfaceContainerHigh = Color(0xFF252D29),
    surfaceContainerHighest = Color(0xFF2C3531),
    outline = Color(0xFF4A5550),
    outlineVariant = Color(0xFF28302C),
)

/** 意味の色。Material の役割に無いもの */
@Immutable
data class Semantic(
    /** 相手 (発信者) */
    val caller: Color,
    /** 相手の吹き出しの地 */
    val callerBubble: Color,
    /** AI */
    val ai: Color,
    val aiBubble: Color,
    /** 自分 */
    val me: Color,
    val meBubble: Color,
    val onMeBubble: Color,
    /** 切る・拒否 */
    val danger: Color,
    /** 通話中を示す点 */
    val live: Color,
)

private val LightSemantic = Semantic(
    caller = Color(0xFFB0680E),
    callerBubble = Color.White,
    ai = Color(0xFF3E4A45),
    aiBubble = Color(0xFFE4E9E6),
    me = Green,
    meBubble = Green,
    onMeBubble = Color.White,
    danger = Color(0xFFE0443C),
    live = Color(0xFFE0443C),
)

private val DarkSemantic = Semantic(
    caller = Color(0xFFF0B25E),
    callerBubble = Color(0xFF1F2623),
    ai = Color(0xFFC3CDC8),
    aiBubble = Color(0xFF2A322E),
    me = Color(0xFF5CCBA3),
    meBubble = Color(0xFF1E7A5B),
    onMeBubble = Color.White,
    danger = Color(0xFFF0605A),
    live = Color(0xFFF0605A),
)

val LocalSemantic = staticCompositionLocalOf { LightSemantic }

private val Base = Typography()
private val FragmentTypography = Base.copy(
    headlineMedium = Base.headlineMedium.copy(fontWeight = FontWeight.Bold, fontSize = 28.sp, lineHeight = 36.sp),
    headlineSmall = Base.headlineSmall.copy(fontWeight = FontWeight.SemiBold),
    titleLarge = Base.titleLarge.copy(fontWeight = FontWeight.SemiBold),
    titleMedium = Base.titleMedium.copy(fontWeight = FontWeight.SemiBold),
    titleSmall = Base.titleSmall.copy(fontWeight = FontWeight.SemiBold),
    labelLarge = Base.labelLarge.copy(fontWeight = FontWeight.SemiBold),
)

/**
 * ⚠**Material You (壁紙由来の動的配色) は既定でoff**。
 *
 * 2026-07-31にエミュレータ (Android 16) で実測したところ、動的配色が効くと
 * アバターと呼び出し理由の吹き出しが**壁紙由来の青**になり、Android 10 の実機で出る緑と
 * 食い違った。端末ごとに配色が変わると「フラグメントの画面」だと分からなくなるうえ、
 * 顧客に配ったときに見た目を説明できない。
 *
 * 端末に馴染ませたくなったら `dynamicColor = true` で戻せる。
 */
@Composable
fun FragmentTheme(
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit,
) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val colors = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> DarkColors
        else -> LightColors
    }
    CompositionLocalProvider(LocalSemantic provides if (dark) DarkSemantic else LightSemantic) {
        MaterialTheme(colorScheme = colors, typography = FragmentTypography, content = content)
    }
}
