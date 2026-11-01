import Foundation

/// What `hard-sources.js` returns for ntk (see `ios/docs/hard-sources.md`).
struct NtkPageState {
    var state = "waiting"

    init(_ json: HardJSON) {
        state = json.string("state")
    }
}

struct NtkChapterState {
    var ready = false
    var blocked = false
    var verification = false

    init(_ json: HardJSON) {
        ready = json.bool("ready")
        blocked = json.bool("blocked")
        verification = json.bool("verification")
    }
}

struct NtkBookInfo {
    var ok = false
    var blocked = false
    var error = ""
    var kind = "novel"
    var title = ""
    var author = ""
    var synopsis = ""
    var cover = ""
    var tags: [String] = []

    init(_ json: HardJSON) {
        ok = json.bool("ok")
        blocked = json.bool("blocked")
        error = json.string("error")
        kind = json.string("kind").isEmpty ? "novel" : json.string("kind")
        title = json.string("title")
        author = json.string("author")
        synopsis = json.string("synopsis")
        cover = json.string("cover")
        tags = json.strings("tags")
    }
}

struct NtkChapterEntry {
    var url = ""
    var episodeId = ""
    var number = 0
    var name = ""

    init(_ json: HardJSON) {
        url = json.string("url")
        episodeId = json.string("episodeId")
        number = json.int("number")
        name = json.string("name")
    }
}

struct NtkChapterListResult {
    var ok = false
    var error = ""
    var chapters: [NtkChapterEntry] = []

    init(_ json: HardJSON) {
        ok = json.bool("ok")
        error = json.string("error")
        chapters = json.objects("chapters").map(NtkChapterEntry.init)
    }
}

struct NtkImage {
    var url = ""
    var name = ""

    init(_ json: HardJSON) {
        url = json.string("url")
        name = json.string("name")
    }
}

struct NtkChapterResult {
    var ok = false
    var blocked = false
    var stage = ""
    var status = 0
    var error = ""
    var chapterName = ""
    var contentText = ""
    /// XHTML fragment already wrapped in `<div class="ntk-content">`; webtoon images point at the `name`s.
    var contentHtml = ""
    var images: [NtkImage] = []

    init(_ json: HardJSON) {
        ok = json.bool("ok")
        blocked = json.bool("blocked")
        stage = json.string("stage")
        status = json.int("status")
        error = json.string("error")
        chapterName = json.string("chapterName")
        contentText = json.string("contentText")
        contentHtml = json.string("contentHtml")
        images = json.objects("images").map(NtkImage.init)
    }
}
