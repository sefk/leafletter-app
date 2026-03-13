package com.kloninger.leafletter.ui

import android.annotation.SuppressLint
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.viewinterop.AndroidView
import com.kloninger.leafletter.Config
import com.kloninger.leafletter.R
import com.kloninger.leafletter.ui.theme.LeafletterGreen
import com.kloninger.leafletter.ui.theme.OnLeafletterGreen

// MARK: - Screen

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CampaignDetailScreen(
    slug: String,
    campaignName: String,
    onBack: () -> Unit,
) {
    var showAbout by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = campaignName,
                        maxLines = 1,
                        overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = stringResource(R.string.back),
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = LeafletterGreen,
                    titleContentColor = OnLeafletterGreen,
                    navigationIconContentColor = OnLeafletterGreen,
                ),
            )
        },
    ) { innerPadding ->
        CampaignWebView(
            url = "${Config.baseUrl}/c/$slug/",
            onAbout = { showAbout = true },
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        )
    }

    if (showAbout) {
        AboutBottomSheet(onDismiss = { showAbout = false })
    }
}

// MARK: - WebView wrapper

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun CampaignWebView(
    url: String,
    onAbout: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val host = remember(url) {
        try {
            java.net.URI(url).host
        } catch (_: Exception) {
            null
        }
    }

    AndroidView(
        modifier = modifier,
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                webViewClient = CampaignWebViewClient(
                    host = host,
                    onAbout = onAbout,
                )
                loadUrl(url)
            }
        },
        // Re-load only when the URL actually changes (navigation pushes a new
        // back-stack entry, so in practice this factory is only called once).
        update = { /* intentionally empty */ },
    )
}

// MARK: - WebViewClient

/**
 * Navigation policy for the campaign WebView:
 *
 * - If the user taps a link to /about/, intercept it and open the About sheet.
 * - Allow navigation within the same host (e.g. following map controls).
 * - Block all cross-domain navigation to prevent accidental external browsing.
 */
private class CampaignWebViewClient(
    private val host: String?,
    private val onAbout: () -> Unit,
) : WebViewClient() {

    override fun shouldOverrideUrlLoading(
        view: WebView,
        request: WebResourceRequest,
    ): Boolean {
        val uri = request.url

        // Intercept /about/ links → show native About sheet
        if (uri.path == "/about/") {
            onAbout()
            return true  // cancel WebView navigation
        }

        // Allow same-host navigation (anchors, map tile requests, etc.)
        if (host != null && uri.host == host) {
            return false  // let WebView handle it
        }

        // Block everything else (external domains)
        return true
    }
}
