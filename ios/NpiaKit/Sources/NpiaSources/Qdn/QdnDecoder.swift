import Foundation
import NpiaCore

/// The glyph decoder's page. A cipher font is matched against a reference of ~9,800 hanzi that takes a few
/// seconds to build, so the decoder lives in its own blank page that outlives the chapter pages: the
/// reference is built once per download and the chapter pages only extract. Chapters hand it the
/// extraction payload as JSON text and get lines back (`hard-sources.js`, `qdn.decoder`).
actor QdnDecoder {
    private let web: any WebEngineProviding
    private let logger: any JobLogging
    private let timing: QdnTiming
    private let gate = HardLock()
    private var page: (any WebPageDriving)?
    private var prepared = false

    init(web: any WebEngineProviding, logger: any JobLogging, timing: QdnTiming) {
        self.web = web
        self.logger = logger
        self.timing = timing
    }

    /// Opens the page, pushes the resources and builds the reference. Safe to call again.
    func prepare() async throws {
        try await gate.run { try await self.prepareLocked() }
    }

    private func prepareLocked() async throws {
        if prepared { return }
        let started = Date()
        let decoderPage: any WebPageDriving
        if let existing = page {
            decoderPage = existing
        } else {
            decoderPage = try await web.newPage(profile: WebProfile.external)
            page = decoderPage
        }
        try await decoderPage.load(URL(string: "about:blank")!, until: .domContentLoaded, timeout: 20)
        try await decoderPage.ensureHard()
        try await Self.push(decoderPage, name: "font", resource: HardScripts.qdnFont)
        try await Self.push(decoderPage, name: "frequency", resource: HardScripts.qdnFrequency)
        try await Self.push(decoderPage, name: "verified", resource: HardScripts.qdnVerified)
        for _ in 0..<max(1, timing.referenceSlices) {
            let reply = try await decoderPage.hardObject("qdn.decoder.prepare(\(timing.referenceSliceMillis))")
            let state = QdnPrepareState(reply)
            if state.done {
                prepared = true
                let elapsed = Int(Date().timeIntervalSince(started) * 1000)
                logger.log(.info, "[qdn] Glyph reference ready (\(state.built) characters, \(elapsed) ms).")
                return
            }
        }
        throw WebEngineError.scriptFailed("the glyph reference did not finish building")
    }

    /// Hands a resource to the page in chunks of about 192 KB of binary; every chunk but the last is a
    /// multiple of 4 base64 characters, as the script requires.
    static func push(_ decoderPage: any WebPageDriving, name: String, resource: String) async throws {
        let encoded = try HardScripts.resource(resource).base64EncodedString()
        var parts: [String] = []
        var index = encoded.startIndex
        while index < encoded.endIndex {
            let end = encoded.index(index, offsetBy: Self.chunkCharacters, limitedBy: encoded.endIndex) ?? encoded.endIndex
            parts.append(String(encoded[index..<end]))
            index = end
        }
        if parts.isEmpty { parts = [""] }
        for (position, part) in parts.enumerated() {
            _ = try await decoderPage.evaluateJSON(
                "return window.__npiaHard.qdn.decoder.putResource(\(HardScripts.literal(name)), \(position), \(parts.count), \(HardScripts.literal(part)));"
            )
        }
    }

    /// SHA-256 digests of the cipher fonts held, so the next extraction can leave them out.
    func knownFonts() async throws -> [String] {
        try await gate.run {
            guard let decoderPage = self.page else { return [] }
            return try await decoderPage.hardStrings("qdn.decoder.knownFonts()")
        }
    }

    /// Decodes an extraction payload (JSON text, as `qdn.extract` returned it).
    func decode(_ payloadJSON: String) async throws -> QdnDecoded {
        try await gate.run {
            try await self.prepareLocked()
            guard let decoderPage = self.page else { throw WebEngineError.scriptFailed("the glyph decoder is closed") }
            let text = try await decoderPage.hardTask(
                "qdn.decoder.decodeJson", argsJSON: "[\(HardScripts.literal(payloadJSON))]",
                pollSeconds: self.timing.decodePoll, maxPolls: self.timing.decodePolls
            )
            return QdnDecoded(HardJSON(text: text))
        }
    }

    func close() async {
        let closing: (any WebPageDriving)? = await gate.run {
            let open = self.page
            self.forget()
            return open
        }
        await closing?.close()
    }

    private func forget() {
        page = nil
        prepared = false
    }

    private static let chunkCharacters = 262_144
}
