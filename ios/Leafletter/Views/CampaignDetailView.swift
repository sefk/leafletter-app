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
            .onAppear {
                // Walk the full view hierarchy to disable any edge-pan gesture
                // recognizer driving swipe-to-pop, regardless of where iOS 26
                // puts the UINavigationController or its equivalent.
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    disableEdgePanGestures()
                }
            }
            .navigationDestination(isPresented: $navigateToAbout) {
                AboutWebView(returnSlug: campaign.slug)
            }
    }
}

private func disableEdgePanGestures() {
    guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
          let window = scene.windows.first(where: \.isKeyWindow) ?? UIApplication.shared.connectedScenes
              .compactMap({ $0 as? UIWindowScene })
              .flatMap(\.windows)
              .first
    else { return }
    disableEdgePanGestures(in: window)
}

private func disableEdgePanGestures(in view: UIView) {
    for gr in view.gestureRecognizers ?? [] where gr is UIScreenEdgePanGestureRecognizer {
        gr.isEnabled = false
    }
    for sub in view.subviews {
        disableEdgePanGestures(in: sub)
    }
}

// MARK: - WKWebView wrapper

private struct CampaignWebView: UIViewRepresentable {
    let url: URL
    let onAbout: () -> Void

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // Hide the hamburger menu — the iOS app has its own navigation.
        let hideMenu = WKUserScript(
            source: """
            var s = document.createElement('style');
            s.textContent = '.hamburger-btn, .hamburger-menu { display: none !important; }';
            document.documentElement.appendChild(s);
            """,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(hideMenu)
        // Inject a viewport meta tag that disables page-level pinch-to-zoom,
        // then scroll past the hero image so the map and "Log a Trip" button
        // are visible on load. The hero is still accessible by scrolling up.
        let script = WKUserScript(
            source: """
            (function() {
                var meta = document.querySelector('meta[name=viewport]');
                if (!meta) { meta = document.createElement('meta'); meta.name = 'viewport'; document.head.appendChild(meta); }
                meta.content = 'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no';

                // Scroll so the toolbar ("Log a Trip") is visible on screen.
                var toolbar = document.querySelector('.toolbar');
                if (toolbar) {
                    toolbar.scrollIntoView({ behavior: 'instant', block: 'end' });
                }
            })();
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(script)
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = false
        webView.navigationDelegate = context.coordinator

        // Pull-to-refresh: attach a UIRefreshControl to the scroll view.
        let refreshControl = UIRefreshControl()
        refreshControl.addTarget(
            context.coordinator,
            action: #selector(Coordinator.handleRefresh(_:)),
            for: .valueChanged
        )
        webView.scrollView.bounces = true
        webView.scrollView.addSubview(refreshControl)
        context.coordinator.refreshControl = refreshControl

        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(host: url.host, onAbout: onAbout) }

    final class Coordinator: NSObject, WKNavigationDelegate {
        let host: String?
        let onAbout: () -> Void
        weak var refreshControl: UIRefreshControl?

        init(host: String?, onAbout: @escaping () -> Void) {
            self.host = host
            self.onAbout = onAbout
        }

        @objc func handleRefresh(_ sender: UIRefreshControl) {
            // Reload the web view; the refresh spinner stops when loading finishes.
            sender.attributedTitle = NSAttributedString(string: "Refreshing…")
            guard let webView = sender.superview?.superview as? WKWebView else {
                sender.endRefreshing()
                return
            }
            webView.reload()
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            refreshControl?.endRefreshing()
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            refreshControl?.endRefreshing()
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
            // Hand off non-web schemes (mailto:, tel:, etc.) to the system.
            if action.navigationType == .linkActivated,
               url.scheme != "http", url.scheme != "https" {
                decisionHandler(.cancel)
                UIApplication.shared.open(url)
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
