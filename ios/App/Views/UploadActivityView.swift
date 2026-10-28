import NpiaUpload
import SwiftUI
import UIKit

/// The progress of one upload: a bar and a status line while it runs, then each link with what
/// it holds and Copy and Share, or the failure with Retry.
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
            case .finished:
                Text(activity.statusLine)
                    .font(.caption)
                    .foregroundStyle(.green)
                ForEach(activity.results, id: \.self) { result in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(result.summary)
                            .font(.caption.weight(.semibold))
                            .lineLimit(2)
                        Text(result.link.absoluteString)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .textSelection(.enabled)
                    }
                }
                HStack(spacing: 16) {
                    Button {
                        if activity.results.count == 1 {
                            UIPasteboard.general.url = activity.results[0].link
                        } else {
                            UIPasteboard.general.string = activity.linkText
                        }
                    } label: {
                        Label(activity.results.count > 1 ? "Copy all" : "Copy", systemImage: "doc.on.doc")
                    }
                    if activity.results.count == 1 {
                        ShareLink(item: activity.results[0].link) { Label("Share", systemImage: "square.and.arrow.up") }
                    } else {
                        ShareLink(item: activity.linkText) { Label("Share all", systemImage: "square.and.arrow.up") }
                    }
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
            // ZIPs or files that no host took, while others went through.
            ForEach(activity.failedUploads, id: \.self) { failure in
                Text("Not uploaded: " + failure)
                    .font(.caption2)
                    .foregroundStyle(.red)
                    .lineLimit(3)
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
