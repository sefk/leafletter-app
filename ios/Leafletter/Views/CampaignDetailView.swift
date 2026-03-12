import SwiftUI
import WebKit

struct CampaignDetailView: View {
    let campaign: Campaign
    @State private var showAbout = false

    private var campaignURL: URL {
        URL(string: Config.baseURL + "/c/\(campaign.slug)/")!
    }

    var body: some View {
        CampaignWebView(url: campaignURL, onAbout: { showAbout = true })
            .navigationTitle(campaign.name)
            .navigationBarTitleDisplayMode(.inline)
            .ignoresSafeArea(edges: .bottom)
            .sheet(isPresented: $showAbout) {
                AboutWebView()
            }
    }
}

// MARK: - WKWebView wrapper

private struct CampaignWebView: UIViewRepresentable {
    let url: URL
    let onAbout: () -> Void

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
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
            // Intercept /about/ links and show the native about sheet
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
