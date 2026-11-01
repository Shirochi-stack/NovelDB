import Foundation

/// What `hard-sources.js` returns for rbooks (see `ios/docs/hard-sources.md`).
struct RbooksProduct {
    var title = ""
    var author = ""
    var synopsis = ""
    var cover = ""
    var publisher = ""
    var tags: [String] = []
    var links: [String] = []
    var titleByID: [String: String] = [:]
    var seriesID = ""
    var diagnostics: [String] = []

    init(_ json: HardJSON) {
        title = json.string("title")
        author = json.string("author")
        synopsis = json.string("synopsis")
        cover = json.string("cover")
        publisher = json.string("publisher")
        tags = json.strings("tags")
        links = json.strings("links")
        titleByID = json.stringMap("titleById")
        seriesID = json.string("seriesId")
        diagnostics = json.strings("diagnostics")
    }
}

struct RbooksLibraryProbe {
    var product = ""
    var unavailable = false
    var adult = false
    var title = ""
    var viewer = false

    init() {}

    init(_ json: HardJSON) {
        product = json.string("product")
        unavailable = json.bool("unavailable")
        adult = json.bool("adult")
        title = json.string("title")
        viewer = json.bool("viewer")
    }
}

struct RbooksApiState {
    var json = false
    var title = ""
    var url = ""
    var challenge = false
    var text = ""

    init() {}

    init(_ object: HardJSON) {
        json = object.bool("json")
        title = object.string("title")
        url = object.string("url")
        challenge = object.bool("challenge")
        text = object.string("text")
    }
}

struct RbooksDiscovery {
    var done = false
    var stalled = false
    /// Opaque; handed back to continue.
    var state: Any?
    var links: [String] = []
    var titleByID: [String: String] = [:]
    var diagnostics: [String] = []
    var isSerial: Bool?
    var error = ""

    init() {}

    init(_ json: HardJSON) {
        done = json.bool("done")
        stalled = json.bool("stalled")
        state = json.any("state")
        links = json.strings("links")
        titleByID = json.stringMap("titleById")
        diagnostics = json.strings("diagnostics")
        isSerial = json.optionalBool("isSerial")
        error = json.string("error")
    }
}

struct RbooksContentState {
    var complete = false
    var found = false
    var ready = false
    var length = 0
    var refused = false
    var url = ""

    init(_ json: HardJSON) {
        complete = json.bool("complete")
        found = json.bool("found")
        ready = json.bool("ready")
        length = json.int("length")
        refused = json.bool("refused")
        url = json.string("url")
    }
}

struct RbooksWall {
    var wall = false
    var offViewer = false
    var refused = false
    var hits = 0
    var url = ""

    init(_ json: HardJSON) {
        wall = json.bool("wall")
        offViewer = json.bool("offViewer")
        refused = json.bool("refused")
        hits = json.int("hits")
        url = json.string("url")
    }
}

struct RbooksImage {
    var url = ""
    var name = ""

    init(_ json: HardJSON) {
        url = json.string("url")
        name = json.string("name")
    }
}

struct RbooksExtract {
    var title = ""
    /// XHTML fragment; images point at the `name`s below.
    var content = ""
    var contentText = ""
    var images: [RbooksImage] = []

    init(_ json: HardJSON) {
        title = json.string("title")
        content = json.string("content")
        contentText = json.string("contentText")
        images = json.objects("images").map(RbooksImage.init)
    }
}
