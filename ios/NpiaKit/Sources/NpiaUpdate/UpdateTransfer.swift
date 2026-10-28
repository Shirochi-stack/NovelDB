import CryptoKit
import Foundation

/// Why an update could not be downloaded.
public enum AppUpdateError: Error, LocalizedError, Equatable, Sendable {
    /// The file's size or SHA-256 differs from the manifest's. The file is deleted.
    case checksumMismatch
    case http(Int)
    case network(String)
    case storage(String)

    public var errorDescription: String? {
        switch self {
        case .checksumMismatch: return "The download did not match its checksum; nothing was installed."
        case .http(let status): return "The download failed: the server answered HTTP \(status)."
        case .network(let text): return "The download failed: \(text)"
        case .storage(let text): return "The download could not be saved: \(text)"
        }
    }
}

/// One streamed download. The bytes go straight into `destination` while their SHA-256 is
/// computed, so the file is never read again and a download that grows past the size the
/// manifest promised is stopped at once. Cancelling the calling task cancels the transfer.
final class UpdateTransfer: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    struct Received: Equatable {
        var size: Int64
        var sha256: String
    }

    private let url: URL
    private let destination: URL
    private let expectedSize: Int64
    private let configuration: URLSessionConfiguration
    private let progress: @Sendable (Int64, Int64) -> Void

    // Shared between the calling task and the delegate queue.
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Received, Error>?
    private var task: URLSessionDataTask?
    private var cancelled = false

    // Used only on the delegate queue, once the task runs.
    private var handle: FileHandle?
    private var hasher = SHA256()
    private var received: Int64 = 0
    private var failure: Error?

    init(
        url: URL, destination: URL, expectedSize: Int64, configuration: URLSessionConfiguration,
        progress: @escaping @Sendable (Int64, Int64) -> Void
    ) {
        self.url = url
        self.destination = destination
        self.expectedSize = expectedSize
        self.configuration = configuration
        self.progress = progress
    }

    /// Runs the download. Throws `CancellationError` when the calling task is cancelled.
    func run() async throws -> Received {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Received, Error>) in
                self.begin(continuation)
            }
        } onCancel: {
            self.cancel()
        }
    }

    private func cancel() {
        lock.lock()
        cancelled = true
        let task = self.task
        lock.unlock()
        task?.cancel()
    }

    private func begin(_ continuation: CheckedContinuation<Received, Error>) {
        lock.lock()
        if cancelled {
            lock.unlock()
            continuation.resume(throwing: CancellationError())
            return
        }
        self.continuation = continuation
        lock.unlock()

        do {
            try? FileManager.default.removeItem(at: destination)
            guard FileManager.default.createFile(atPath: destination.path, contents: nil) else {
                throw AppUpdateError.storage("the file could not be created")
            }
            handle = try FileHandle(forWritingTo: destination)
        } catch {
            finish(.failure(error))
            return
        }

        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 60)
        request.setValue("NovelDB", forHTTPHeaderField: "User-Agent")
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: queue)
        let task = session.dataTask(with: request)
        lock.lock()
        self.task = task
        let stop = cancelled
        lock.unlock()
        if stop { task.cancel() }
        task.resume()
    }

    private func finish(_ result: Result<Received, Error>) {
        lock.lock()
        let continuation = self.continuation
        self.continuation = nil
        lock.unlock()
        try? handle?.close()
        handle = nil
        switch result {
        case .success(let value): continuation?.resume(returning: value)
        case .failure(let error): continuation?.resume(throwing: error)
        }
    }

    // MARK: - URLSessionDataDelegate

    func urlSession(
        _ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            failure = AppUpdateError.http(http.statusCode)
            completionHandler(.cancel)
        } else {
            completionHandler(.allow)
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard failure == nil, let handle else { return }
        received += Int64(data.count)
        if received > expectedSize {
            failure = AppUpdateError.checksumMismatch
            dataTask.cancel()
            return
        }
        do {
            try handle.write(contentsOf: data)
        } catch {
            failure = AppUpdateError.storage(error.localizedDescription)
            dataTask.cancel()
            return
        }
        hasher.update(data: data)
        progress(received, expectedSize)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        session.finishTasksAndInvalidate()
        if let failure {
            finish(.failure(failure))
        } else if let error {
            if (error as? URLError)?.code == .cancelled {
                finish(.failure(CancellationError()))
            } else {
                finish(.failure(AppUpdateError.network(error.localizedDescription)))
            }
        } else {
            let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
            finish(.success(Received(size: received, sha256: digest)))
        }
    }
}
