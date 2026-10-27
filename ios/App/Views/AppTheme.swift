import SwiftUI
import UIKit

/// Light / dark choice; AMOLED is dark with pure-black backgrounds.
enum ThemeMode: String, CaseIterable, Identifiable {
    case system, light, dark, amoled

    static let storageKey = "theme"
    var id: String { rawValue }

    var label: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        case .amoled: return "AMOLED"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark, .amoled: return .dark
        }
    }
}

/// Colour accents shared with the Glossarion mobile app: Material 3 tonal-spot schemes from
/// each seed. Halgakos Rose is the default.
enum Accent: String, CaseIterable, Identifiable {
    case halgakosRose = "halgakos_rose"
    case desktopBlue = "desktop_blue"
    case libraryViolet = "library_violet"

    static let storageKey = "accent"
    var id: String { rawValue }

    var label: String {
        switch self {
        case .halgakosRose: return "Halgakos Rose"
        case .desktopBlue: return "Desktop Blue"
        case .libraryViolet: return "Library Violet"
        }
    }

    var seed: Color {
        switch self {
        case .halgakosRose: return Color(hex: 0xE18F98)
        case .desktopBlue: return Color(hex: 0x5A9FD4)
        case .libraryViolet: return Color(hex: 0x6C63FF)
        }
    }

    struct Palette {
        let primary: Color
        let background: Color
        let row: Color
    }

    var palette: Palette {
        switch self {
        case .halgakosRose:
            return Palette(primary: .dynamic(0x8B4D55, 0xF7B6BC),
                           background: .dynamic(0xFFF0F0, 0x130C0D),
                           row: .dynamic(0xFFFFFF, 0x281D1E))
        case .desktopBlue:
            return Palette(primary: .dynamic(0x366385, 0xAACAE9),
                           background: .dynamic(0xF1F4F9, 0x0C0E11),
                           row: .dynamic(0xFFFFFF, 0x1B2025))
        case .libraryViolet:
            return Palette(primary: .dynamic(0x5C5A8B, 0xC5C2EE),
                           background: .dynamic(0xF6F2FA, 0x0E0E12),
                           row: .dynamic(0xFFFFFF, 0x1F1F26))
        }
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(uiColor: UIColor(hex: hex))
    }

    /// A colour that follows the light / dark appearance.
    static func dynamic(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light) })
    }
}

extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                  blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}

/// Paints a list or form with the chosen accent's background colours (pure black for AMOLED).
private struct ThemedBackground: ViewModifier {
    @AppStorage(ThemeMode.storageKey) private var theme = ThemeMode.system.rawValue
    @AppStorage(Accent.storageKey) private var accent = Accent.halgakosRose.rawValue

    func body(content: Content) -> some View {
        let palette = (Accent(rawValue: accent) ?? .halgakosRose).palette
        let amoled = ThemeMode(rawValue: theme) == .amoled
        content
            .scrollContentBackground(.hidden)
            .background(amoled ? Color.black : palette.background)
    }
}

/// Applies the chosen theme mode and accent to the whole app.
private struct AppTheme: ViewModifier {
    @AppStorage(ThemeMode.storageKey) private var theme = ThemeMode.system.rawValue
    @AppStorage(Accent.storageKey) private var accent = Accent.halgakosRose.rawValue

    func body(content: Content) -> some View {
        content
            .tint((Accent(rawValue: accent) ?? .halgakosRose).palette.primary)
            .preferredColorScheme((ThemeMode(rawValue: theme) ?? .system).colorScheme)
    }
}

extension View {
    func themedBackground() -> some View { modifier(ThemedBackground()) }
    func appTheme() -> some View { modifier(AppTheme()) }
}
