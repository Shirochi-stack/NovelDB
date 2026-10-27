import NpiaUpload
import SwiftUI
import UIKit

/// The progress of one upload: a bar and a status line while it runs, then the link with Copy
/// and Share, or the failure with Retry.
struct UploadActivityView: View {
    let activity: UploadActivity
    var retry: () -> Void
    var dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            switch activity.phase {
            case .running:
                ProgressView(value: activity.fraction)
                Text(activity.statusLine)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            case .finished(let result):
                Text(activity.statusLine)
                    .font(.caption)
                    .foregroundStyle(.green)
                    .lineLimit(3)
                    .textSelection(.enabled)
                HStack(spacing: 16) {
                    Button {
                        UIPasteboard.general.url = result.link
                    } label: {
                        Label("Copy", systemImage: "doc.on.doc")
                    }
                    ShareLink(item: result.link) { Label("Share", systemImage: "square.and.arrow.up") }
                    Spacer()
                    Button(action: dismiss) { Label("Dismiss", systemImage: "xmark") }
                        .labelStyle(.iconOnly)
                }
                .font(.caption)
                .buttonStyle(.borderless)
            case .failed:
                Text(activity.statusLine)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(4)
                    .textSelection(.enabled)
                HStack(spacing: 16) {
                    Button(action: retry) { Label("Retry", systemImage: "arrow.clockwise") }
                    Spacer()
                    Button(action: dismiss) { Label("Dismiss", systemImage: "xmark") }
                        .labelStyle(.iconOnly)
                }
                .font(.caption)
                .buttonStyle(.borderless)
            }
            // Hosts that refused the file before the one in use, so a fallback is visible.
            ForEach(activity.failures, id: \.self) { failure in
                Text(failure)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(.top, 2)
    }
}
