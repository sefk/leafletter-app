package com.kloninger.leafletter.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.kloninger.leafletter.ApiClient
import com.kloninger.leafletter.Campaign
import com.kloninger.leafletter.R
import com.kloninger.leafletter.ui.theme.LeafletterGreen
import com.kloninger.leafletter.ui.theme.OnLeafletterGreen
import com.kloninger.leafletter.ui.theme.StatusGray
import com.kloninger.leafletter.ui.theme.StatusGreen
import com.kloninger.leafletter.ui.theme.StatusYellow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// MARK: - Screen

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CampaignListScreen(
    onCampaignClick: (Campaign) -> Unit,
) {
    var campaigns by remember { mutableStateOf<List<Campaign>>(emptyList()) }
    var isLoading by remember { mutableStateOf(true) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var showAbout by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        isLoading = true
        errorMessage = null
        try {
            val result = withContext(Dispatchers.IO) { ApiClient.fetchCampaigns() }
            campaigns = result
        } catch (e: Exception) {
            errorMessage = e.message ?: "Unknown error"
        } finally {
            isLoading = false
        }
    }

    LaunchedEffect(Unit) { load() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.app_name)) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = LeafletterGreen,
                    titleContentColor = OnLeafletterGreen,
                    actionIconContentColor = OnLeafletterGreen,
                ),
                actions = {
                    IconButton(
                        onClick = { scope.launch { load() } },
                        enabled = !isLoading,
                    ) {
                        Icon(
                            imageVector = Icons.Filled.Refresh,
                            contentDescription = stringResource(R.string.refresh),
                        )
                    }
                },
            )
        },
    ) { innerPadding ->
        PullToRefreshBox(
            isRefreshing = isLoading,
            onRefresh = { scope.launch { load() } },
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            when {
                isLoading && campaigns.isEmpty() -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                }

                errorMessage != null -> {
                    ErrorState(
                        message = errorMessage!!,
                        onRetry = { scope.launch { load() } },
                    )
                }

                campaigns.isEmpty() -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            text = stringResource(R.string.no_campaigns_title),
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                else -> {
                    LazyColumn(modifier = Modifier.fillMaxSize()) {
                        item {
                            BannerRow(onAbout = { showAbout = true })
                        }
                        items(campaigns, key = { it.id }) { campaign ->
                            CampaignCard(
                                campaign = campaign,
                                onClick = { onCampaignClick(campaign) },
                            )
                            HorizontalDivider()
                        }
                    }
                }
            }
        }
    }

    if (showAbout) {
        AboutBottomSheet(onDismiss = { showAbout = false })
    }
}

// MARK: - Banner

@Composable
private fun BannerRow(onAbout: () -> Unit) {
    val annotated = buildAnnotatedString {
        append(stringResource(R.string.banner_text))
        append(" ")
        withStyle(
            SpanStyle(
                fontWeight = FontWeight.Bold,
                textDecoration = TextDecoration.Underline,
                color = OnLeafletterGreen,
            )
        ) {
            append(stringResource(R.string.about_link_text))
        }
    }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(LeafletterGreen)
            .clickable { onAbout() }
            .padding(horizontal = 16.dp, vertical = 12.dp),
    ) {
        Text(
            text = annotated,
            color = OnLeafletterGreen.copy(alpha = 0.9f),
            fontSize = 13.sp,
            lineHeight = 18.sp,
        )
    }
}

// MARK: - Campaign card

@Composable
private fun CampaignCard(
    campaign: Campaign,
    onClick: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
    ) {
        // Hero image (16:9 aspect ratio placeholder when no URL)
        if (campaign.heroImageUrl != null) {
            AsyncImage(
                model = campaign.heroImageUrl,
                contentDescription = campaign.name,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f),
            )
        }

        Column(
            modifier = Modifier.padding(
                horizontal = if (campaign.heroImageUrl != null) 12.dp else 16.dp,
                vertical = 8.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(
                text = campaign.name,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )

            campaign.dateRangeText?.let { dates ->
                Text(
                    text = dates,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (!campaign.isReady) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    StatusDot(mapStatus = campaign.mapStatus)
                    Text(
                        text = stringResource(R.string.map_generating),
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFFE65100), // deep orange
                    )
                }
            } else {
                // Ready — show a subtle green dot
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    StatusDot(mapStatus = campaign.mapStatus)
                }
            }
        }
    }
}

// MARK: - Status dot

@Composable
private fun StatusDot(mapStatus: String) {
    val color = when (mapStatus) {
        "ready" -> StatusGreen
        "warning" -> StatusYellow
        else -> StatusGray
    }
    Box(
        modifier = Modifier
            .size(8.dp)
            .background(color = color, shape = MaterialTheme.shapes.small),
    )
}

// MARK: - Error state

@Composable
private fun ErrorState(message: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = stringResource(R.string.error_loading_title),
            style = MaterialTheme.typography.titleMedium,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = message,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(16.dp))
        Button(onClick = onRetry) {
            Text(stringResource(R.string.retry))
        }
    }
}
