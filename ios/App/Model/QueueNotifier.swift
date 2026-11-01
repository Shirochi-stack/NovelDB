import Foundation
import NpiaCore
import UIKit
import UserNotifications

/// Registers the notification delegate as soon as the app launches, so a tap on a notification
/// that started the app is delivered.
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = QueueNotifier.shared
        return true
    }
}

/// The local notifications of the download queue, and where tapping one goes.
///
/// One notification at a time (the same request identifier replaces the last one): "downloads
/// continue in the background" when the app is left with downloads running, and the summary
/// when a run ends while the app is not in front. Its `userInfo` holds a `QueueTarget`: the
/// Queue's combined log, or the job's own page when the run was a single book.
final class QueueNotifier: NSObject, UNUserNotificationCenterDelegate {
    static let shared = QueueNotifier()
    static let identifier = "npia.queue.run"

    private var handler: (@MainActor (QueueTarget) -> Void)?
    private var pending: QueueTarget?

    private override init() {
        super.init()
    }

    /// Where taps are sent. A tap that started the app before this was set is delivered now.
    @MainActor
    func setHandler(_ handler: @escaping @MainActor (QueueTarget) -> Void) {
        self.handler = handler
        if let target = pending {
            pending = nil
            handler(target)
        }
    }

    /// Asks once, the first time a download is queued.
    func requestAuthorizationIfNeeded() {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .notDetermined else { return }
            _ = try? await center.requestAuthorization(options: [.alert, .sound])
        }
    }

    func post(title: String, body: String, target: QueueTarget) {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            content.threadIdentifier = "npia.queue"
            content.userInfo = target.userInfo
            let request = UNNotificationRequest(identifier: Self.identifier, content: content, trigger: nil)
            try? await center.add(request)
        }
    }

    /// Takes the queue's notification off the lock screen and out of the notification list.
    func clearDelivered() {
        let center = UNUserNotificationCenter.current()
        center.removeDeliveredNotifications(withIdentifiers: [Self.identifier])
        center.removePendingNotificationRequests(withIdentifiers: [Self.identifier])
    }

    // MARK: - UNUserNotificationCenterDelegate

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        guard let target = QueueTarget(userInfo: response.notification.request.content.userInfo) else { return }
        await MainActor.run {
            if let handler {
                handler(target)
            } else {
                pending = target
            }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        // Posted only while the app is in the background; nothing to show over the open app.
        []
    }
}
