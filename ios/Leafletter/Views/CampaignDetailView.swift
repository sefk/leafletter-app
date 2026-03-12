import SwiftUI
import WebKit

struct CampaignDetailView: View {
    let campaign: Campaign

    private var campaignURL: URL {
        URL(string: Config.baseURL + "/c/\(campaign.slug)/")!
    }

    var body: some View {
        CampaignWebView(url: campaignURL)
            .navigationTitle(campaign.name)
            .navigationBarTitleDisplayMode(.inline)
            .ignoresSafeArea(edges: .bottom)
    }
}

// MARK: - WKWebView wrapper

private struct CampaignWebView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(host: url.host) }

    final class Coordinator: NSObject, WKNavigationDelegate {
        let host: String?

        init(host: String?) { self.host = host }

        func webView(_ webView: WKWebView,
                     decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            // Allow same-host navigation; block external links
            guard let linkHost = action.request.url?.host else {
                decisionHandler(.allow)
                return
            }
            decisionHandler(linkHost == host ? .allow : .cancel)
        }
    }
}
