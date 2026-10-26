import Foundation
import UIKit
#if canImport(BackgroundTasks)
import BackgroundTasks
#endif

/// Keeps long downloads alive: optionally holds the screen awake while jobs run, asks for a
/// short background grace period when the app leaves the foreground, and on iOS 26 runs each
/// job as a `BGContinuedProcessingTask` with system progress UI.
@MainActor
final class BackgroundActivity {
    static let keepAwakeKey = "keepScreenAwake"
    static let continuedProcessingKey = "continueInBackground"
    /// Matches `BGTaskSchedulerPermittedIdentifiers` in project.yml.
    static let taskPrefix = "com.shirochi.npiamobile.download."

    private var running: Set<UUID> = []
    private var graceTasks: [UUID: UIBackgroundTaskIdentifier] = [:]
    private var continued: [UUID: AnyObject] = [:]

    var keepAwake: Bool { UserDefaults.standard.object(forKey: Self.keepAwakeKey) as? Bool ?? true }
    var continueInBackground: Bool { UserDefaults.standard.object(forKey: Self.continuedProcessingKey) as? Bool ?? true }

    static var supportsContinuedProcessing: Bool {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) { return true }
        #endif
        return false
    }

    func jobStarted(_ id: UUID, title: String, cancel: @escaping @MainActor () -> Void) {
        running.insert(id)
        updateIdleTimer()
        graceTasks[id] = UIApplication.shared.beginBackgroundTask(withName: "download-\(id.uuidString)") { [weak self] in
            Task { @MainActor in self?.endGrace(id) }
        }
        guard continueInBackground else { return }
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            submitContinuedTask(id, title: title, cancel: cancel)
        }
        #endif
    }

    func jobProgress(_ id: UUID, fraction: Double, status: String) {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *), let task = continued[id] as? BGContinuedProcessingTask {
            task.progress.totalUnitCount = 1000
            task.progress.completedUnitCount = Int64(min(1, max(0, fraction)) * 1000)
            task.updateTitle(task.title, subtitle: status)
        }
        #endif
    }

    func jobEnded(_ id: UUID, success: Bool) {
        running.remove(id)
        updateIdleTimer()
        endGrace(id)
        #if compiler(>=6.2)
        if #available(iOS 26.0, *), let task = continued[id] as? BGContinuedProcessingTask {
            task.setTaskCompleted(success: success)
        }
        #endif
        continued[id] = nil
    }

    func settingsChanged() {
        updateIdleTimer()
    }

    private func updateIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = keepAwake && !running.isEmpty
    }

    private func endGrace(_ id: UUID) {
        guard let task = graceTasks.removeValue(forKey: id) else { return }
        UIApplication.shared.endBackgroundTask(task)
    }

    #if compiler(>=6.2)
    @available(iOS 26.0, *)
    private func submitContinuedTask(_ id: UUID, title: String, cancel: @escaping @MainActor () -> Void) {
        let identifier = Self.taskPrefix + id.uuidString
        let registered = BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: .main) { [weak self] task in
            guard let task = task as? BGContinuedProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            MainActor.assumeIsolated {
                guard let self, self.running.contains(id) else {
                    task.setTaskCompleted(success: true)
                    return
                }
                self.continued[id] = task
                task.expirationHandler = {
                    Task { @MainActor in cancel() }
                }
            }
        }
        guard registered else { return }
        let request = BGContinuedProcessingTaskRequest(identifier: identifier, title: title, subtitle: "Starting")
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            // Not fatal: the job keeps running in the foreground.
        }
    }
    #endif
}
