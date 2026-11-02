import SwiftUI

/// A book in the Library: every output sharing a name ("Title.epub", "Title.txt", a "Title/" TXT folder).
struct LibraryBook: Identifiable {
    let name: String
    let files: [URL]
    let modified: Date
    var id: String { name.lowercased() }

    static let extensions: Set<String> = ["epub", "pdf", "txt", "html", "htm", "cbz", "zip"]

    /// The books in the app's download folder, newest first.
    static func scan(_ folder: URL) -> [LibraryBook] {
        let manager = FileManager.default
        let keys: [URLResourceKey] = [.isDirectoryKey, .contentModificationDateKey]
        let entries = (try? manager.contentsOfDirectory(at: folder, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles])) ?? []
        var groups: [String: (name: String, files: [URL], modified: Date)] = [:]
        for url in entries {
            let isFolder = (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
            if isFolder {
                let inside = (try? manager.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? []
                guard inside.contains(where: { ["txt", "html", "jpg", "png", "webp"].contains($0.pathExtension.lowercased()) }) else { continue }
            } else if !extensions.contains(url.pathExtension.lowercased()) {
                continue
            }
            let name = isFolder ? url.lastPathComponent : url.deletingPathExtension().lastPathComponent
            let date = (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            var group = groups[name.lowercased()] ?? (name, [], .distantPast)
            group.files.append(url)
            group.modified = max(group.modified, date)
            groups[name.lowercased()] = group
        }
        return groups.values.map { LibraryBook(name: $0.name, files: $0.files.sorted { $0.lastPathComponent < $1.lastPathComponent }, modified: $0.modified) }
            .sorted { $0.modified > $1.modified }
    }
}

/// The Library tab: the books downloaded on this device, with the same actions as a finished download.
struct LibraryView: View {
    @Environment(AppModel.self) private var model
    @State private var books: [LibraryBook] = []
    @State private var query = ""
    @State private var deleting: LibraryBook?

    private var shown: [LibraryBook] {
        let text = query.trimmingCharacters(in: .whitespaces)
        return text.isEmpty ? books : books.filter { $0.name.localizedCaseInsensitiveContains(text) }
    }

    var body: some View {
        List {
            if shown.isEmpty {
                Text(books.isEmpty ? "No books yet. Downloads are saved here and show up as soon as they finish." : "No book matches.")
                    .foregroundStyle(.secondary)
            }
            ForEach(shown) { book in
                Section {
                    ForEach(book.files, id: \.self) { file in
                        FileRow(file: file, jobID: model.jobs.first { $0.files(in: model.outputDirectory).contains(file) }?.id)
                    }
                } header: {
                    HStack {
                        Text(book.name).font(.headline).textCase(nil).lineLimit(3)
                        Spacer()
                        Button(role: .destructive) { deleting = book } label: { Image(systemName: "trash") }
                    }
                } footer: {
                    Text(book.modified.formatted(date: .abbreviated, time: .shortened))
                }
            }
        }
        .searchable(text: $query, prompt: "Find a book")
        .navigationTitle("Library")
        .refreshable { reload() }
        .onAppear { reload() }
        .onChange(of: model.jobs.filter { !$0.state.isActive }.count) { _, _ in reload() }
        .confirmationDialog("Delete \(deleting?.name ?? "")?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                deleting?.files.forEach { try? FileManager.default.removeItem(at: $0) }
                deleting = nil
                reload()
            }
        }
    }

    private func reload() {
        books = LibraryBook.scan(model.outputDirectory)
    }
}
