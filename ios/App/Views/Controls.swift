import NpiaCore
import SwiftUI

/// Multi-select for output formats; at least one stays selected.
struct FormatPicker: View {
    @Binding var formats: [OutputFormat]

    var body: some View {
        ForEach(OutputFormat.allCases, id: \.self) { format in
            Toggle(format.label, isOn: Binding(
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

// MARK: - Compact controls for the Add tab

/// A capsule toggle that fits several to a row.
struct ChipToggle: View {
    let title: String
    @Binding var isOn: Bool

    var body: some View {
        Button {
            isOn.toggle()
        } label: {
            HStack(spacing: 4) {
                if isOn { Image(systemName: "checkmark").font(.caption2.weight(.bold)) }
                Text(title).font(.footnote).lineLimit(1)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(isOn ? Color.accentColor.opacity(0.22) : Color.secondary.opacity(0.12)))
            .overlay(Capsule().stroke(isOn ? Color.accentColor : Color.secondary.opacity(0.35), lineWidth: 1))
            .foregroundStyle(isOn ? Color.accentColor : Color.primary)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

/// Lays children out left to right, wrapping onto new lines.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(0, rows.count - 1))
        let width = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var current = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            if !current.indices.isEmpty, current.width + spacing + size.width > width {
                rows.append(current)
                current = Row()
            }
            current.width = current.indices.isEmpty ? size.width : current.width + spacing + size.width
            current.height = max(current.height, size.height)
            current.indices.append(index)
        }
        if !current.indices.isEmpty { rows.append(current) }
        return rows
    }
}

/// "Title  - 4 +" in one short row.
struct CompactStepper: View {
    let title: String
    @Binding var value: Int
    let range: ClosedRange<Int>

    var body: some View {
        HStack(spacing: 6) {
            Text(title).font(.subheadline).lineLimit(1)
            Button { value = max(range.lowerBound, value - 1) } label: { Image(systemName: "minus.circle") }
                .disabled(value <= range.lowerBound)
            Text("\(value)").font(.subheadline.monospacedDigit()).frame(minWidth: 22)
            Button { value = min(range.upperBound, value + 1) } label: { Image(systemName: "plus.circle") }
                .disabled(value >= range.upperBound)
        }
        .buttonStyle(.borderless)
        .fixedSize()
    }
}

/// A small labelled decimal box ("Min [0.5]").
struct CompactDecimal: View {
    let title: String
    @Binding var value: Double

    var body: some View {
        HStack(spacing: 3) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            TextField(title, value: $value, format: .number)
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.center)
                .font(.subheadline.monospacedDigit())
                .frame(width: 52)
                .textFieldStyle(.roundedBorder)
        }
    }
}

/// A small labelled whole-number box; empty means off ("From [  ]").
/// A positive whole number, or empty for none. A wrong entry stays in the field, marked red, until
/// it is fixed (only valid numbers are saved); `marked` marks it red for the caller's own reason.
struct CompactNumber: View {
    let title: String
    @Binding var value: Int?
    var marked = false
    @State private var text: String?

    private var shown: String { text ?? value.map(String.init) ?? "" }
    private var invalid: Bool {
        let trimmed = shown.trimmingCharacters(in: .whitespaces)
        return !trimmed.isEmpty && (Int(trimmed).map { $0 < 1 } ?? true)
    }

    var body: some View {
        HStack(spacing: 3) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            TextField("-", text: Binding(
                get: { shown },
                set: { new in
                    text = new
                    let trimmed = new.trimmingCharacters(in: .whitespaces)
                    if trimmed.isEmpty {
                        value = nil
                    } else if let number = Int(trimmed), number > 0 {
                        value = number
                    }
                }
            ))
            .keyboardType(.numberPad)
            .multilineTextAlignment(.center)
            .font(.subheadline.monospacedDigit())
            .frame(width: 56)
            .textFieldStyle(.roundedBorder)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.red, lineWidth: invalid || marked ? 2 : 0))
            .onChange(of: value) { _, newValue in
                // Changed elsewhere (another source's settings): show it, unless the field is mid-edit on it.
                if let typed = text, Int(typed.trimmingCharacters(in: .whitespaces)) != newValue, !invalid { text = nil }
            }
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
