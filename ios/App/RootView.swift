import NpiaPipeline
import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model
    @State private var urlText = ""

    var body: some View {
        NavigationStack {
            List {
                Section("Add") {
                    TextField("Novel URL", text: $urlText)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                    Button("Download") {
                        model.enqueue(urlText)
                        urlText = ""
                    }
                    .disabled(!model.canDownload(urlText))
                }
                Section("Jobs") {
                    if model.jobs.isEmpty {
                        Text("No downloads yet").foregroundStyle(.secondary)
                    }
                    ForEach(model.jobs) { job in
                        JobRow(job: job)
                            .swipeActions { Button("Cancel", role: .destructive) { model.cancel(job.id) } }
                    }
                }
            }
            .navigationTitle("Npia Mobile")
        }
    }
}

private struct JobRow: View {
    let job: JobItem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(job.title ?? job.url.absoluteString).lineLimit(1)
            Text(status).font(.caption).foregroundStyle(.secondary)
            ForEach(job.links, id: \.self) { link in
                ShareLink(item: link) { Label(link.absoluteString, systemImage: "link").font(.caption) }
            }
        }
    }

    private var status: String {
        switch job.phase {
        case .queued: return "Queued"
        case .fetchingBook: return "Reading book info"
        case .fetchingChapters(let done, let total): return "Chapters \(done)/\(total)"
        case .downloadingImages(let done, let total): return "Images \(done)/\(total)"
        case .writing(let format): return "Writing \(format.rawValue.uppercased())"
        case .uploading(let name): return "Uploading \(name)"
        case .finished: return "Done: \(job.files.map(\.lastPathComponent).joined(separator: ", "))"
        case .failed(let message): return message
        }
    }
}
