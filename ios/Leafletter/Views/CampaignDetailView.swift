import SwiftUI
import UIKit
import WebKit

struct CampaignDetailView: View {
    let campaign: Campaign
    @State private var navigateToAbout = false

    private var campaignURL: URL {
        URL(string: Config.baseURL + "/c/\(campaign.slug)/")!
    }

    var body: some View {
        CampaignWebView(url: campaignURL, onAbout: { navigateToAbout = true })
            .navigationTitle(campaign.name)
            .navigationBarTitleDisplayMode(.inline)
            .ignoresSafeArea(edges: .bottom)
            .navigationDestination(isPresented: $navigateToAbout) {
                // Pass the campaign slug so the About page's "back" link
                // returns here rather than to the campaign list.
                AboutWebView(returnSlug: campaign.slug)
            }
    }
}

// MARK: - WKWebView wrapper

private struct CampaignWebView: UIViewRepresentable {
    let url: URL
    let onAbout: () -> Void

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // Inject a viewport meta tag that disables page-level pinch-to-zoom.
        // The Leaflet map handles its own touch/pinch events independently.
        let script = WKUserScript(
            source: """
            (function() {
                var meta = document.querySelector('meta[name=viewport]');
                if (!meta) { meta = document.createElement('meta'); meta.name = 'viewport'; document.head.appendChild(meta); }
                meta.content = 'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no';
            })();
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(script)
        let webView = WKWebView(frame: .zero, configuration: config)
        // Disable the swipe-back gesture — the native NavBar back button handles back navigation.
        webView.allowsBackForwardNavigationGestures = false
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(host: url.host, onAbout: onAbout) }

    final class Coordinator: NSObject, WKNavigationDelegate {
        let host: String?
        let onAbout: () -> Void

        init(host: String?, onAbout: @escaping () -> Void) {
            self.host = host
            self.onAbout = onAbout
        }

        func webView(_ webView: WKWebView,
                     decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else {
                decisionHandler(.allow)
                return
            }
            // Intercept /about/ links and push the native About view.
            if action.navigationType == .linkActivated, url.path == "/about/" {
                decisionHandler(.cancel)
                onAbout()
                return
            }
            // Allow same-host navigation; block external links
            guard let linkHost = url.host else {
                decisionHandler(.allow)
                return
            }
            decisionHandler(linkHost == host ? .allow : .cancel)
        }
    }
}
