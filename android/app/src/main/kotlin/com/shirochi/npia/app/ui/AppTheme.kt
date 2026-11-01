package com.shirochi.npia.app.ui

import android.content.Context
import android.os.Build
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color

/** Light / dark choice; AMOLED is dark with pure-black surfaces. */
enum class ThemeMode(val raw: String, val label: String) {
    SYSTEM("system", "System"), LIGHT("light", "Light"), DARK("dark", "Dark"), AMOLED("amoled", "AMOLED");

    companion object {
        fun of(raw: String?): ThemeMode = entries.firstOrNull { it.raw == raw } ?: SYSTEM
    }
}

/**
 * Colour accents. The first three are the Glossarion mobile accents (Material 3 tonal-spot
 * schemes from their seeds; Halgakos Rose keeps the Horn Plum tertiary in light mode).
 * Wallpaper uses Android 12+ dynamic colour.
 */
enum class Accent(val raw: String, val label: String, val seed: Color) {
    HALGAKOS_ROSE("halgakos_rose", "Halgakos Rose", Color(0xFFE18F98)),
    DESKTOP_BLUE("desktop_blue", "Desktop Blue", Color(0xFF5A9FD4)),
    LIBRARY_VIOLET("library_violet", "Library Violet", Color(0xFF6C63FF)),
    WALLPAPER("wallpaper", "Wallpaper", Color(0xFF808080));

    companion object {
        fun of(raw: String?): Accent = entries.firstOrNull { it.raw == raw } ?: HALGAKOS_ROSE
        val available: List<Accent> get() = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) entries else entries - WALLPAPER
    }
}

fun appColorScheme(context: Context, accent: Accent, mode: ThemeMode, systemDark: Boolean): ColorScheme {
    val dark = when (mode) {
        ThemeMode.SYSTEM -> systemDark
        ThemeMode.LIGHT -> false
        ThemeMode.DARK, ThemeMode.AMOLED -> true
    }
    val base = when {
        accent == Accent.WALLPAPER && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        accent == Accent.DESKTOP_BLUE -> if (dark) desktopBlueDark else desktopBlueLight
        accent == Accent.LIBRARY_VIOLET -> if (dark) libraryVioletDark else libraryVioletLight
        else -> if (dark) halgakosRoseDark else halgakosRoseLight
    }
    if (mode != ThemeMode.AMOLED) return base
    return base.copy(
        background = Color.Black,
        surface = Color.Black,
        surfaceDim = Color.Black,
        surfaceContainerLowest = Color.Black,
        surfaceContainerLow = Color(0xFF0A0A0A),
        surfaceContainer = Color(0xFF0A0A0A),
    )
}

private val halgakosRoseLight = lightColorScheme(
    primary = Color(0xFF8B4D55),
    onPrimary = Color(0xFFFFF7F7),
    primaryContainer = Color(0xFFFEAFB7),
    onPrimaryContainer = Color(0xFF642D35),
    secondary = Color(0xFF77575A),
    onSecondary = Color(0xFFFFF7F7),
    secondaryContainer = Color(0xFFFFD9DC),
    onSecondaryContainer = Color(0xFF684A4D),
    tertiary = Color(0xFF5B3D57),
    onTertiary = Color(0xFFFFF7FA),
    tertiaryContainer = Color(0xFFFECEF7),
    onTertiaryContainer = Color(0xFF664264),
    error = Color(0xFFA83836),
    onError = Color(0xFFFFF7F6),
    errorContainer = Color(0xFFFA746F),
    onErrorContainer = Color(0xFF6E0A12),
    background = Color(0xFFFFF8F7),
    onBackground = Color(0xFF3D2F30),
    surface = Color(0xFFFFF8F7),
    onSurface = Color(0xFF3D2F30),
    surfaceVariant = Color(0xFFF5DDDE),
    onSurfaceVariant = Color(0xFF6C5B5C),
    outline = Color(0xFF897677),
    outlineVariant = Color(0xFFC2ADAE),
    inverseSurface = Color(0xFF130C0D),
    inverseOnSurface = Color(0xFFA79A9A),
    inversePrimary = Color(0xFFFEAFB7),
    surfaceDim = Color(0xFFECD5D6),
    surfaceBright = Color(0xFFFFF8F7),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFFFF0F0),
    surfaceContainer = Color(0xFFFEE9EA),
    surfaceContainerHigh = Color(0xFFF9E3E4),
    surfaceContainerHighest = Color(0xFFF5DDDE),
    scrim = Color(0xFF000000),
)

private val halgakosRoseDark = darkColorScheme(
    primary = Color(0xFFF7B6BC),
    onPrimary = Color(0xFF603338),
    primaryContainer = Color(0xFF75454A),
    onPrimaryContainer = Color(0xFFFFDADD),
    secondary = Color(0xFFE5BDC0),
    onSecondary = Color(0xFF55393B),
    secondaryContainer = Color(0xFF503437),
    onSecondaryContainer = Color(0xFFDEB5B8),
    tertiary = Color(0xFFFFE3F9),
    onTertiary = Color(0xFF6F4B6C),
    tertiaryContainer = Color(0xFFFECEF7),
    onTertiaryContainer = Color(0xFF664264),
    error = Color(0xFFFA746F),
    onError = Color(0xFF490006),
    errorContainer = Color(0xFF871F21),
    onErrorContainer = Color(0xFFFF9993),
    background = Color(0xFF130C0D),
    onBackground = Color(0xFFF8E0E1),
    surface = Color(0xFF130C0D),
    onSurface = Color(0xFFF8E0E1),
    surfaceVariant = Color(0xFF302223),
    onSurfaceVariant = Color(0xFFBBA6A7),
    outline = Color(0xFF847172),
    outlineVariant = Color(0xFF544445),
    inverseSurface = Color(0xFFFFF8F7),
    inverseOnSurface = Color(0xFF5D5353),
    inversePrimary = Color(0xFF845156),
    surfaceDim = Color(0xFF130C0D),
    surfaceBright = Color(0xFF37292A),
    surfaceContainerLowest = Color(0xFF000000),
    surfaceContainerLow = Color(0xFF1A1112),
    surfaceContainer = Color(0xFF211718),
    surfaceContainerHigh = Color(0xFF281D1E),
    surfaceContainerHighest = Color(0xFF302223),
    scrim = Color(0xFF000000),
)

private val desktopBlueLight = lightColorScheme(
    primary = Color(0xFF366385),
    onPrimary = Color(0xFFF6F9FF),
    primaryContainer = Color(0xFF9AC6ED),
    onPrimaryContainer = Color(0xFF094060),
    secondary = Color(0xFF516170),
    onSecondary = Color(0xFFF6F9FF),
    secondaryContainer = Color(0xFFD4E4F6),
    onSecondaryContainer = Color(0xFF445362),
    tertiary = Color(0xFF5B5B85),
    onTertiary = Color(0xFFFBF7FF),
    tertiaryContainer = Color(0xFFCECDFE),
    onTertiaryContainer = Color(0xFF43446B),
    error = Color(0xFFA83836),
    onError = Color(0xFFFFF7F6),
    errorContainer = Color(0xFFFA746F),
    onErrorContainer = Color(0xFF6E0A12),
    background = Color(0xFFF8F9FD),
    onBackground = Color(0xFF2D3339),
    surface = Color(0xFFF8F9FD),
    onSurface = Color(0xFF2D3339),
    surfaceVariant = Color(0xFFDDE3EB),
    onSurfaceVariant = Color(0xFF596067),
    outline = Color(0xFF757B83),
    outlineVariant = Color(0xFFACB2BA),
    inverseSurface = Color(0xFF0C0E11),
    inverseOnSurface = Color(0xFF9B9DA1),
    inversePrimary = Color(0xFF9AC6ED),
    surfaceDim = Color(0xFFD5DAE3),
    surfaceBright = Color(0xFFF8F9FD),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF1F4F9),
    surfaceContainer = Color(0xFFEAEEF5),
    surfaceContainerHigh = Color(0xFFE4E8F0),
    surfaceContainerHighest = Color(0xFFDDE3EB),
    scrim = Color(0xFF000000),
)

private val desktopBlueDark = darkColorScheme(
    primary = Color(0xFFAACAE9),
    onPrimary = Color(0xFF22435C),
    primaryContainer = Color(0xFF36556F),
    onPrimaryContainer = Color(0xFFCDE6FF),
    secondary = Color(0xFFB8C8D9),
    onSecondary = Color(0xFF324250),
    secondaryContainer = Color(0xFF2E3D4B),
    onSecondaryContainer = Color(0xFFB1C1D2),
    tertiary = Color(0xFFDEDCFF),
    onTertiary = Color(0xFF4C4C75),
    tertiaryContainer = Color(0xFFCECDFE),
    onTertiaryContainer = Color(0xFF43446B),
    error = Color(0xFFFA746F),
    onError = Color(0xFF490006),
    errorContainer = Color(0xFF871F21),
    onErrorContainer = Color(0xFFFF9993),
    background = Color(0xFF0C0E11),
    onBackground = Color(0xFFE0E6EE),
    surface = Color(0xFF0C0E11),
    onSurface = Color(0xFFE0E6EE),
    surfaceVariant = Color(0xFF20262C),
    onSurfaceVariant = Color(0xFFA5ACB3),
    outline = Color(0xFF70767D),
    outlineVariant = Color(0xFF42494F),
    inverseSurface = Color(0xFFF8F9FD),
    inverseOnSurface = Color(0xFF535559),
    inversePrimary = Color(0xFF42627D),
    surfaceDim = Color(0xFF0C0E11),
    surfaceBright = Color(0xFF272D33),
    surfaceContainerLowest = Color(0xFF000000),
    surfaceContainerLow = Color(0xFF101418),
    surfaceContainer = Color(0xFF151A1F),
    surfaceContainerHigh = Color(0xFF1B2025),
    surfaceContainerHighest = Color(0xFF20262C),
    scrim = Color(0xFF000000),
)

private val libraryVioletLight = lightColorScheme(
    primary = Color(0xFF5C5A8B),
    onPrimary = Color(0xFFFBF7FF),
    primaryContainer = Color(0xFFCAC6FF),
    onPrimaryContainer = Color(0xFF403F6E),
    secondary = Color(0xFF5E5D72),
    onSecondary = Color(0xFFFBF7FF),
    secondaryContainer = Color(0xFFE3E0F9),
    onSecondaryContainer = Color(0xFF515064),
    tertiary = Color(0xFF765377),
    onTertiary = Color(0xFFFFF7FA),
    tertiaryContainer = Color(0xFFFED2FD),
    onTertiaryContainer = Color(0xFF664568),
    error = Color(0xFFA8364B),
    onError = Color(0xFFFFF7F7),
    errorContainer = Color(0xFFF97386),
    onErrorContainer = Color(0xFF6E0523),
    background = Color(0xFFFCF8FE),
    onBackground = Color(0xFF32313A),
    surface = Color(0xFFFCF8FE),
    onSurface = Color(0xFF32313A),
    surfaceVariant = Color(0xFFE4E1ED),
    onSurfaceVariant = Color(0xFF5F5E68),
    outline = Color(0xFF7B7984),
    outlineVariant = Color(0xFFB3B0BC),
    inverseSurface = Color(0xFF0E0E12),
    inverseOnSurface = Color(0xFF9E9CA2),
    inversePrimary = Color(0xFFCAC6FF),
    surfaceDim = Color(0xFFDCD8E4),
    surfaceBright = Color(0xFFFCF8FE),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF6F2FA),
    surfaceContainer = Color(0xFFF0ECF6),
    surfaceContainerHigh = Color(0xFFEAE6F1),
    surfaceContainerHighest = Color(0xFFE4E1ED),
    scrim = Color(0xFF000000),
)

private val libraryVioletDark = darkColorScheme(
    primary = Color(0xFFC5C2EE),
    onPrimary = Color(0xFF3E3C60),
    primaryContainer = Color(0xFF504F74),
    onPrimaryContainer = Color(0xFFE3E0FF),
    secondary = Color(0xFFC7C4DD),
    onSecondary = Color(0xFF3F3E52),
    secondaryContainer = Color(0xFF3B394D),
    onSecondaryContainer = Color(0xFFC0BDD5),
    tertiary = Color(0xFFFFE7FC),
    onTertiary = Color(0xFF6F4D70),
    tertiaryContainer = Color(0xFFFED2FD),
    onTertiaryContainer = Color(0xFF664568),
    error = Color(0xFFF97386),
    onError = Color(0xFF490013),
    errorContainer = Color(0xFF871C34),
    onErrorContainer = Color(0xFFFF97A3),
    background = Color(0xFF0E0E12),
    onBackground = Color(0xFFE7E4F0),
    surface = Color(0xFF0E0E12),
    onSurface = Color(0xFFE7E4F0),
    surfaceVariant = Color(0xFF26252E),
    onSurfaceVariant = Color(0xFFACA9B5),
    outline = Color(0xFF76747F),
    outlineVariant = Color(0xFF484750),
    inverseSurface = Color(0xFFFCF8FE),
    inverseOnSurface = Color(0xFF565459),
    inversePrimary = Color(0xFF5D5B81),
    surfaceDim = Color(0xFF0E0E12),
    surfaceBright = Color(0xFF2C2B34),
    surfaceContainerLowest = Color(0xFF000000),
    surfaceContainerLow = Color(0xFF131318),
    surfaceContainer = Color(0xFF19191F),
    surfaceContainerHigh = Color(0xFF1F1F26),
    surfaceContainerHighest = Color(0xFF26252E),
    scrim = Color(0xFF000000),
)
