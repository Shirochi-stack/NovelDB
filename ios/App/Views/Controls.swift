import NpiaCore
import SwiftUI

/// Multi-select for output formats; at least one stays selected.
struct FormatPicker: View {
    @Binding var formats: [OutputFormat]

    var body: some View {
        ForEach(OutputFormat.allCases, id: \.self) { format in
            Toggle(format.rawValue.uppercased(), isOn: Binding(
                get: { formats.contains(format) },
                set: { on in
                    if on {
                        if !formats.contains(format) {
                            formats = OutputFormat.allCases.filter { formats.contains($0) || $0 == format }
                        }
                    } else if formats.count > 1 {
                        formats.removeAll { $0 == format }
                    }
                }
            ))
        }
    }
}

/// An optional number with an on/off switch (chapter ranges, size targets).
struct OptionalNumberField<Value: Numeric & Comparable>: View where Value: LosslessStringConvertible {
    let title: String
    @Binding var value: Value?
    let defaultValue: Value

    var body: some View {
        Toggle(title, isOn: Binding(get: { value != nil }, set: { value = $0 ? (value ?? defaultValue) : nil }))
        if value != nil {
            TextField(title, text: Binding(
                get: { value.map { String($0) } ?? "" },
                set: { text in if let parsed = Value(text.trimmingCharacters(in: .whitespaces)) { value = parsed } }
            ))
            .keyboardType(.decimalPad)
            .padding(.leading)
        }
    }
}

/// A labelled decimal field.
struct DecimalField: View {
    let title: String
    @Binding var value: Double

    var body: some View {
        LabeledContent(title) {
            TextField(title, value: $value, format: .number)
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 120)
        }
    }
}

/// A per-source override: off uses the shared value, on edits a local copy.
struct OverrideRow<Value, Editor: View>: View {
    let title: String
    @Binding var value: Value?
    let shared: Value
    let describe: (Value) -> String
    @ViewBuilder let editor: (Binding<Value>) -> Editor

    var body: some View {
        Toggle(isOn: Binding(get: { value != nil }, set: { value = $0 ? shared : nil })) {
            VStack(alignment: .leading) {
                Text(title)
                if value == nil {
                    Text("Shared: \(describe(shared))").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        if value != nil {
            editor(Binding(get: { value ?? shared }, set: { value = $0 }))
                .padding(.leading)
        }
    }
}

extension ImageFormat {
    var label: String {
        switch self {
        case .webp: return "WebP"
        case .jpeg: return "JPEG"
        case .png: return "PNG"
        case .original: return "Keep original"
        }
    }
}
