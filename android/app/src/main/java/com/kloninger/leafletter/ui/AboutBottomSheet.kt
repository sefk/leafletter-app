package com.kloninger.leafletter.ui

import android.annotation.SuppressLint
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.kloninger.leafletter.Config
import com.kloninger.leafletter.R

/**
 * Modal bottom sheet that shows the /about/ page in a WebView.
 *
 * Navigation policy mirrors the iOS AboutWKWebViewRepresentable:
 * - If the user taps the home/root link (path "/" or "") the sheet is dismissed.
 * - Other navigation within the about page is allowed.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AboutBottomSheet(onDismiss: () -> Unit) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        modifier = Modifier.fillMaxHeight(0.92f),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = stringResource(R.string.about),
                style = MaterialTheme.typography.titleMedium,
            )
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.done))
            }
        }

        HorizontalDivider()

        AboutWebView(
            onNavigateHome = onDismiss,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        )
    }
}

// MARK: - WebView

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun AboutWebView(
    onNavigateHome: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val aboutUrl = "${Config.baseUrl}/about/"
    val baseUrl = Config.baseUrl

    AndroidView(
        modifier = modifier,
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                webViewClient = AboutWebViewClient(
                    baseUrl = baseUrl,
                    onNavigateHome = onNavigateHome,
                )
                loadUrl(aboutUrl)
            }
        },
    )
}

// MARK: - WebViewClient

/**
 * Dismisses the About sheet if the user navigates to the app home page,
 * matching the iOS behaviour in AboutWKWebViewRepresentable.
 */
private class AboutWebViewClient(
    private val baseUrl: String,
    private val onNavigateHome: () -> Unit,
) : WebViewClient() {

    override fun shouldOverrideUrlLoading(
        view: WebView,
        request: WebResourceRequest,
    ): Boolean {
        val uri = request.url
        val path = uri.path ?: ""

        // Dismiss sheet when user taps a link back to home
        if ((path == "/" || path.isEmpty()) && uri.toString().startsWith(baseUrl)) {
            onNavigateHome()
            return true
        }

        return false  // allow all other navigation within the about page
    }
}
