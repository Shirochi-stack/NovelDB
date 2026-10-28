import NpiaUpdate
import SwiftUI
import UIKit

/// The "update available" sheet: Update, Release notes and Later; then the download with its
/// progress and a Cancel; then the checked file for the share sheet and Files. iOS apps cannot
/// install themselves, so the sheet says what to do with the file.
struct AppUpdateSheet: View {
    @Environment(AppModel.self) private var model
    @State private var savingToFiles = false

    var body: some View {
        let updates = model.appUpdate
        NavigationStack {
            Form {
                if let offer = updates.offer {
                    Section {
                        Text(offer.title).font(.headline)
                        LabeledContent("In use", value: updates.build.statusText)
                        LabeledContent("Size", value: ByteCountFormatter.string(fromByteCount: offer.asset.size, countStyle: .file))
                    }
                    actions(offer, updates)
                } else {
                    Section {
                        Text("No update is waiting.")
                        Button("Close") { updates.dismissOffer() }
                    }
                }
            }
            .themedBackground()
            .navigationTitle("App update")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
        .sheet(isPresented: $savingToFiles) {
            if case .ready(let file) = updates.phase {
                FileSaver(url: file) { savingToFiles = false }
            }
        }
    }

    private static let explanation = "iOS apps cannot install themselves. Once the download is checked, send the file to SideStore, AltStore or a similar tool from the share sheet, or save it to Files."

    @ViewBuilder
    private func actions(_ offer: AppUpdate, _ updates: AppUpdateController) -> some View {
        switch updates.phase {
        case .idle:
            Section {
                Button("Update") { updates.startDownload() }
                Link("Release notes", destination: offer.releaseURL)
                Button("Later", role: .cancel) { updates.dismissOffer() }
            } footer: {
                Text(Self.explanation)
            }
        case .downloading:
            Section {
                ProgressView(value: updates.fraction)
                Text("Downloading... \(Int((updates.fraction * 100).rounded()))%")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Cancel", role: .destructive) { updates.cancelDownload() }
            } footer: {
                Text("The file's size and SHA-256 are checked against the release before it is offered.")
            }
        case .ready(let file):
            Section {
                Label("Downloaded and checked", systemImage: "checkmark.seal")
                // Up here, not in a footer, so it shows without scrolling the sheet.
                Text(Self.explanation).font(.caption).foregroundStyle(.secondary)
                ShareLink(item: file) { Label("Share the IPA", systemImage: "square.and.arrow.up") }
                Button { savingToFiles = true } label: { Label("Save to Files", systemImage: "folder") }
                Link("Release notes", destination: offer.releaseURL)
                Button("Done", role: .cancel) { updates.dismissOffer() }
            }
        case .failed(let text):
            Section {
                Text(text).foregroundStyle(.red)
                Button("Try again") { updates.startDownload() }
                Link("Release notes", destination: offer.releaseURL)
                Button("Close", role: .cancel) { updates.dismissOffer() }
            }
        }
    }
}

/// "Save to Files": the system's export picker for one file, which it copies.
struct FileSaver: UIViewControllerRepresentable {
    let url: URL
    let finished: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(finished: finished)
    }

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forExporting: [url], asCopy: true)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let finished: () -> Void

        init(finished: @escaping () -> Void) {
            self.finished = finished
        }

        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            finished()
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            finished()
        }
    }
}
