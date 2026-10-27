import Foundation
import Observation

/// A book picked from the catalog website, waiting on the Add tab.
struct CartItem: Codable, Equatable, Identifiable {
    var url: String
    var title: String = ""
    var source: String = ""
    var id: String { url }
}

/// Links collected from the Catalog tab, like a shopping cart; the Add tab downloads them.
@MainActor
@Observable
final class LinkCart {
    private(set) var items: [CartItem]
    private let store = JSONFileStore<[CartItem]>(name: "link-cart.json")

    init() {
        items = store.load() ?? []
    }

    func add(_ item: CartItem) {
        guard !items.contains(where: { $0.url == item.url }) else { return }
        items.append(item)
        store.save(items)
    }

    func remove(_ url: String) {
        items.removeAll { $0.url == url }
        store.save(items)
    }

    func removeAll(_ urls: Set<String>) {
        items.removeAll { urls.contains($0.url) }
        store.save(items)
    }

    func clear() {
        items = []
        store.save(items)
    }
}
