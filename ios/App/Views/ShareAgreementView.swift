import SwiftUI

/// Shown the first time the user taps Upload. Nothing is uploaded until they confirm, and the
/// answer is remembered, so it is never asked again.
struct ShareAgreementView: View {
    @Environment(AppModel.self) private var model
    @AppStorage(AppModel.shareAgreementKey) private var accepted = false
    @State private var confirmed = false
    let pending: PendingUpload

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Upload creates a public link that anyone who has it can open. Only upload files you own or are authorised to share \u{2014} for example works you wrote, files you have permission to distribute, or public-domain works. Sharing copyrighted books without permission may break the law and the source site's terms, and you are responsible for what you upload.")
                }
                Section {
                    Toggle("I confirm I'm authorised to share the files I upload.", isOn: $confirmed)
                }
                Section {
                    Button("Agree and upload") {
                        accepted = true
                        model.pendingUpload = nil
                        model.upload(pending.request)
                    }
                    .disabled(!confirmed)
                    Button("Cancel", role: .cancel) {
                        model.pendingUpload = nil
                    }
                }
            }
            .themedBackground()
            .navigationTitle("Before you share")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
    }
}

/// An upload waiting for the sharing agreement.
struct PendingUpload: Identifiable, Equatable {
    let id = UUID()
    let request: UploadRequest
}
