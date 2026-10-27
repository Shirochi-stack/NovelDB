import SwiftUI
import UIKit
import WebKit

/// Ends text editing when the user taps anywhere outside a text field, across the whole app.
/// Taps still reach the view underneath, and taps inside web pages are left to the page.
@MainActor
final class KeyboardDismissTap: NSObject, UIGestureRecognizerDelegate {
    static let shared = KeyboardDismissTap()
    private var windows: Set<ObjectIdentifier> = []

    func install(in window: UIWindow) {
        guard windows.insert(ObjectIdentifier(window)).inserted else { return }
        let tap = UITapGestureRecognizer(target: self, action: #selector(tapped(_:)))
        tap.cancelsTouchesInView = false
        tap.delegate = self
        window.addGestureRecognizer(tap)
    }

    @objc private func tapped(_ recognizer: UITapGestureRecognizer) {
        recognizer.view?.endEditing(true)
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
        var view = touch.view
        while let current = view {
            if current is UITextField || current is UITextView || current is WKWebView { return false }
            view = current.superview
        }
        return true
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool {
        true
    }
}

/// Zero-size view that installs `KeyboardDismissTap` on the window hosting the SwiftUI tree.
struct KeyboardDismissInstaller: UIViewRepresentable {
    func makeUIView(context: Context) -> UIView { WindowProbe() }
    func updateUIView(_ uiView: UIView, context: Context) {}

    private final class WindowProbe: UIView {
        override func didMoveToWindow() {
            super.didMoveToWindow()
            if let window { KeyboardDismissTap.shared.install(in: window) }
        }
    }
}
