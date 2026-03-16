import SwiftUI
import UIKit

@main
struct LeafletterApp: App {
    var body: some Scene {
        WindowGroup {
            CampaignListView()
        }
    }
}

// Disable the swipe-to-pop gesture app-wide. Every screen in this app uses
// WKWebView content where a right swipe is meaningful (Leaflet map panning,
// lasso drawing), and a native back button is always available in the nav bar.
extension UINavigationController {
    override open func viewDidLoad() {
        super.viewDidLoad()
        interactivePopGestureRecognizer?.isEnabled = false
    }
}
