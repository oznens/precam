package com.precam.kpssturkce.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val Indigo = Color(0xFF3F51B5)
private val IndigoDark = Color(0xFF283593)
private val Amber = Color(0xFFFFB300)
private val Teal = Color(0xFF00897B)

private val LightColors = lightColorScheme(
    primary = Indigo,
    onPrimary = Color.White,
    secondary = Teal,
    onSecondary = Color.White,
    tertiary = Amber,
    onTertiary = Color(0xFF1A1A1A),
    background = Color(0xFFF5F6FA),
    surface = Color.White,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF7986CB),
    onPrimary = Color(0xFF0E1330),
    secondary = Color(0xFF4DB6AC),
    onSecondary = Color(0xFF06302B),
    tertiary = Amber,
    onTertiary = Color(0xFF1A1A1A),
    background = Color(0xFF101218),
    surface = Color(0xFF1A1D26),
)

@Composable
fun KpssTurkceTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = Typography(),
        content = content
    )
}
