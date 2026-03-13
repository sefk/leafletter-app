package com.kloninger.leafletter.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Leafletter green: matches iOS BannerView background rgb(26,107,61) → #1A6B3D
val LeafletterGreen = Color(0xFF1A6B3D)
val LeafletterGreenDark = Color(0xFF145230)
val LeafletterGreenContainer = Color(0xFFC8E6C9)
val OnLeafletterGreen = Color(0xFFFFFFFF)

// Status dot colours
val StatusGreen = Color(0xFF4CAF50)
val StatusYellow = Color(0xFFFFC107)
val StatusGray = Color(0xFF9E9E9E)

private val LeafletterColorScheme = lightColorScheme(
    primary = LeafletterGreen,
    onPrimary = OnLeafletterGreen,
    primaryContainer = LeafletterGreenContainer,
    secondary = LeafletterGreenDark,
    onSecondary = OnLeafletterGreen,
)

@Composable
fun LeafletterTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = LeafletterColorScheme,
        content = content,
    )
}
