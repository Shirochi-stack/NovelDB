import Foundation

/// What `hard-sources.js` returns for mpia (see `ios/docs/hard-sources.md`).
struct MpiaDetail {
    var ok = false
    var status = 0
    var error = ""
    var login = false
    var id = ""
    var title = ""
    var author = ""
    var synopsis = ""
    var cover = ""
    var tags: [String] = []
    var finished = false
    var total = 0

    init() {}

    init(_ json: HardJSON) {
        ok = json.bool("ok")
        status = json.int("status")
        error = json.string("error")
        login = json.bool("login")
        id = json.string("id")
        title = json.string("title")
        author = json.string("author")
        synopsis = json.string("synopsis")
        cover = json.string("cover")
        tags = json.strings("tags")
        finished = json.bool("finished")
        total = json.int("total")
    }
}

struct MpiaChapterRow {
    var id = ""
    var order = 0
    var title = ""
    var url = ""
    var isPaid = false
    var accessible = false
    var date = ""

    init(_ json: HardJSON) {
        id = json.string("id")
        order = json.int("order")
        title = json.string("title")
        url = json.string("url")
        isPaid = json.bool("isPaid")
        accessible = json.bool("accessible")
        date = json.string("date")
    }
}

struct MpiaChapterList {
    var ok = false
    var status = 0
    var error = ""
    var total = 0
    var list: [MpiaChapterRow] = []

    init(_ json: HardJSON) {
        ok = json.bool("ok")
        status = json.int("status")
        error = json.string("error")
        total = json.int("total")
        list = json.objects("list").map(MpiaChapterRow.init)
    }
}

struct MpiaImage {
    var url = ""
    var name = ""

    init(_ json: HardJSON) {
        url = json.string("url")
        name = json.string("name")
    }
}

struct MpiaExtract {
    var chapterName = ""
    var contentText = ""
    /// XHTML fragment already wrapped in `<div class="mpia-content">`; images point at the `name`s below.
    var contentHtml = ""
    var images: [MpiaImage] = []
    var locked = false
    var error = ""

    init(_ json: HardJSON) {
        chapterName = json.string("chapterName")
        contentText = json.string("contentText")
        contentHtml = json.string("contentHtml")
        images = json.objects("images").map(MpiaImage.init)
        locked = json.bool("locked")
        error = json.string("error")
    }
}

struct MpiaReaderState {
    var ready = false
    var denied = false
    var found = false

    init(_ json: HardJSON) {
        ready = json.bool("ready")
        denied = json.bool("denied")
        found = json.bool("found")
    }
}
