import Foundation
import NpiaCore

/// Downloaded chapter bodies stay on disk while an open Library reader follows the job.
public enum LibraryLive {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var books: [String: [Session]] = [:]

    public static func begin(_ file: URL, referer: String = "") -> Session {
        lock.withLock {
            let key = file.standardizedFileURL.resolvingSymlinksInPath().path
            let session = Session(referer: referer)
            var history = books[key] ?? []
            history.append(session)
            if history.count > 2 { history.removeFirst().close() }
            books[key] = history
            return session
        }
    }

    public static func saved(_ file: URL, spineCount: Int) {
        lock.withLock { books[file.standardizedFileURL.resolvingSymlinksInPath().path]?.last?.finish(saved: true, spineCount: spineCount) }
    }

    public static func handle(_ request: [String: Any]) -> String {
        lock.withLock {
            let address = request["url"] as? String ?? ""
            let file = URL(string: address)?.standardizedFileURL.resolvingSymlinksInPath()
            let history = file?.isFileURL == true ? books[file!.path] ?? [] : []
            let key = request["key"] as? String
            let session = request["session"] as? String ?? ""
            let book = key == nil ? history.last : history.first { $0.id == session }
            let result = book?.snapshot(session: session, after: request["after"] as? Int ?? 0, key: key) ?? [:]
            let data = (try? JSONSerialization.data(withJSONObject: result)) ?? Data("{}".utf8)
            return String(decoding: data, as: UTF8.self)
        }
    }

    public final class Session: @unchecked Sendable {
        public let id = UUID().uuidString
        private let folder = FileManager.default.temporaryDirectory.appendingPathComponent("noveldb-live-" + UUID().uuidString)
        private let lock = NSLock()
        private var chapters: [ChapterRef] = []
        private var indices: [String: Int] = [:]
        private var states: [Int: (String, Int)] = [:]
        private var version = 0
        private var active = true
        private var saved = false
        private var savedOffset: Int?
        private let referer: String

        init(referer: String) {
            self.referer = referer
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        }
        private func refKey(_ ref: ChapterRef) -> String { "\(ref.isNotice):\(ref.id)" }

        public func book(_ selected: [ChapterRef]) {
            lock.withLock {
                chapters = selected
                indices = [:]
                version += 1
                for (index, ref) in selected.enumerated() {
                    indices[refKey(ref)] = index
                    states[index] = ("pending", version)
                }
            }
        }

        public func chapter(_ result: ChapterResult) {
            lock.withLock {
                guard let index = indices[refKey(result.ref)] else { return }
                var state = "unavailable"
                if case .content(let content) = result {
                    var html = content.html
                    for image in content.images {
                        var replacement = image.url.absoluteString
                        if let data = image.data {
                            let ext = (image.filename as NSString).pathExtension.lowercased()
                            let mime = ["png": "image/png", "webp": "image/webp", "gif": "image/gif", "svg": "image/svg+xml"][ext] ?? "image/jpeg"
                            replacement = "data:\(mime);base64," + data.base64EncodedString()
                        }
                        html = html.replacingOccurrences(of: "\"\(image.filename)\"", with: "\"\(replacement)\"")
                            .replacingOccurrences(of: "'\(image.filename)'", with: "'\(replacement)'")
                    }
                    do {
                        try html.write(to: folder.appendingPathComponent("\(index).html"), atomically: true, encoding: .utf8)
                        state = "ready"
                    } catch { }
                }
                version += 1
                states[index] = (state, version)
            }
        }

        public func finish(saved: Bool = false, spineCount: Int? = nil) {
            lock.withLock {
                active = false
                self.saved = self.saved || saved
                if let spineCount { savedOffset = max(0, spineCount - chapters.count) }
                version += 1
                for index in Array(states.keys) where states[index]?.0 == "pending" { states[index] = ("unavailable", version) }
            }
        }

        func snapshot(session: String, after: Int, key: String?) -> [String: Any] {
            lock.withLock {
                if let key {
                    guard let index = Int(key), chapters.indices.contains(index), states[index]?.0 == "ready",
                          let html = try? String(contentsOf: folder.appendingPathComponent("\(index).html"), encoding: .utf8) else {
                        return ["error": "Chapter is not available"]
                    }
                    return ["html": html, "name": chapters[index].title, "imageReferer": referer]
                }
                let full = session != id || after < 1
                var result: [String: Any] = ["session": id, "version": version, "active": active, "saved": saved]
                if let savedOffset { result["savedOffset"] = savedOffset }
                if full && !chapters.isEmpty {
                    result["chapters"] = chapters.enumerated().map { index, ref in
                        ["key": String(index), "name": ref.title, "identity": refKey(ref)]
                    }
                }
                result["states"] = states.filter { full || $0.value.1 > after }.map { index, state in
                    ["key": String(index), "state": state.0]
                }
                return result
            }
        }

        func close() { lock.withLock { try? FileManager.default.removeItem(at: folder) } }
        deinit { try? FileManager.default.removeItem(at: folder) }
    }
}
