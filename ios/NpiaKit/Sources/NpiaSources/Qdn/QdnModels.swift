import Foundation

/// What `hard-sources.js` returns for qdn (see `ios/docs/hard-sources.md`).
struct QdnPageState {
    var state = "waiting"
    var url = ""
    var title = ""
    var text = ""
    var catalogCount = 0
    /// The page's layout viewport in CSS pixels (the desktop reader is read at 1280 x 800).
    var viewportWidth = 0
    var viewportHeight = 0

    init() {}

    init(_ json: HardJSON) {
        state = json.has("state") ? json.string("state") : "waiting"
        url = json.string("url")
        title = json.string("title")
        text = json.string("text")
        catalogCount = json.int("catalogCount")
        if let viewport = json.object("viewport") {
            viewportWidth = viewport.int("width")
            viewportHeight = viewport.int("height")
        }
    }
}

struct QdnCatalogEntry: Equatable {
    var url = ""
    var name = ""
    var isVip = false

    init(url: String = "", name: String = "", isVip: Bool = false) {
        self.url = url
        self.name = name
        self.isVip = isVip
    }

    init(_ json: HardJSON) {
        url = json.string("url")
        name = json.string("name")
        isVip = json.bool("isVip")
    }
}

struct QdnBookPage {
    var title = ""
    var author = ""
    var coverURL = ""
    var description = ""
    var tags: [String] = []
    var chapters: [QdnCatalogEntry] = []
    var bookURL = ""
    /// The chapter count the page announces; the page itself lists only the first chapters of a long book.
    var totalChapters = 0
    /// The page that lists every chapter.
    var catalogURL = ""

    init(_ json: HardJSON) {
        title = json.string("title")
        author = json.string("author")
        coverURL = json.string("coverUrl")
        description = json.string("description")
        tags = json.strings("tags")
        chapters = json.objects("chapters").map(QdnCatalogEntry.init)
        bookURL = json.string("bookUrl")
        totalChapters = json.int("totalChapters")
        catalogURL = json.string("catalogUrl")
    }
}

struct QdnCatalogState {
    var state = "waiting"
    var count = 0

    init() {}

    init(_ json: HardJSON) {
        state = json.has("state") ? json.string("state") : "waiting"
        count = json.int("count")
    }
}

struct QdnChapterState {
    var ready = false
    var blocked = false
    var verification = false
    var encrypted = false

    init(_ json: HardJSON) {
        ready = json.bool("ready")
        blocked = json.bool("blocked")
        verification = json.bool("verification")
        encrypted = json.bool("encrypted")
    }
}

struct QdnEncryptionState {
    var encrypted = false
    var rendered = false
    var failed = false
    var blocked = false
    var words = 0

    init() {}

    init(_ json: HardJSON) {
        encrypted = json.bool("encrypted")
        rendered = json.bool("rendered")
        failed = json.bool("failed")
        blocked = json.bool("blocked")
        words = json.int("words")
    }
}

/// A plain chapter read: `lines`, or an `error` (`locked` with a `reason`, `verification`, ...).
struct QdnRead {
    var lines: [String] = []
    var error: String?
    var reason: String?
    var chars = 0
    var expected = 0

    init(_ json: HardJSON) {
        lines = json.strings("lines")
        error = json.has("error") ? json.string("error") : nil
        reason = json.has("reason") ? json.string("reason") : nil
        chars = json.int("chars")
        expected = json.int("expected")
    }
}

struct QdnDecodeStats {
    var decoded = 0
    var verifiedGlyphs = 0
    var shapeMatchedGlyphs = 0
    var lowConfidence = 0
    var missingFonts = 0
    var suspiciousPassthrough = 0
    var unstableParagraphs = 0

    init() {}

    init(_ json: HardJSON) {
        decoded = json.int("decoded")
        verifiedGlyphs = json.int("verifiedGlyphs")
        shapeMatchedGlyphs = json.int("shapeMatchedGlyphs")
        lowConfidence = json.int("lowConfidence")
        missingFonts = (json.any("missingFonts") as? [Any])?.count ?? 0
        suspiciousPassthrough = json.int("suspiciousPassthrough")
        unstableParagraphs = (json.any("unstableParagraphs") as? [Any])?.count ?? 0
    }
}

struct QdnDecoded {
    var lines: [String] = []
    var stats = QdnDecodeStats()

    init() {}

    init(_ json: HardJSON) {
        lines = json.strings("lines")
        stats = json.object("stats").map(QdnDecodeStats.init) ?? QdnDecodeStats()
    }

    /// Capture problems worth a second try: unstable paragraphs or fonts that were not available.
    var issues: Int { stats.unstableParagraphs + stats.missingFonts }
}

struct QdnPrepareState {
    var done = false
    var built = 0
    var total = 0

    init(_ json: HardJSON) {
        done = json.bool("done")
        built = json.int("built")
        total = json.int("total")
    }
}
