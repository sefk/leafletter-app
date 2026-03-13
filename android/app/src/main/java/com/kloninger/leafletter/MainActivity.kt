package com.kloninger.leafletter

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.core.view.WindowCompat
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.kloninger.leafletter.ui.CampaignDetailScreen
import com.kloninger.leafletter.ui.CampaignListScreen
import com.kloninger.leafletter.ui.theme.LeafletterTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            LeafletterTheme {
                LeafletterNavHost()
            }
        }
    }
}

// MARK: - Navigation

private object Routes {
    const val CAMPAIGN_LIST = "campaigns"
    const val CAMPAIGN_DETAIL = "campaigns/{slug}/{name}"

    fun detail(slug: String, name: String) =
        "campaigns/${slug}/${java.net.URLEncoder.encode(name, "UTF-8")}"
}

@Composable
private fun LeafletterNavHost() {
    val navController = rememberNavController()

    NavHost(
        navController = navController,
        startDestination = Routes.CAMPAIGN_LIST,
    ) {
        composable(Routes.CAMPAIGN_LIST) {
            CampaignListScreen(
                onCampaignClick = { campaign ->
                    navController.navigate(Routes.detail(campaign.slug, campaign.name))
                }
            )
        }

        composable(
            route = Routes.CAMPAIGN_DETAIL,
            arguments = listOf(
                navArgument("slug") { type = NavType.StringType },
                navArgument("name") { type = NavType.StringType },
            )
        ) { backStackEntry ->
            val slug = backStackEntry.arguments?.getString("slug") ?: ""
            val name = java.net.URLDecoder.decode(
                backStackEntry.arguments?.getString("name") ?: "", "UTF-8"
            )
            CampaignDetailScreen(
                slug = slug,
                campaignName = name,
                onBack = { navController.popBackStack() },
            )
        }
    }
}
