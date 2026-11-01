import Foundation
import NpiaCore

/// `ImageSettings.longImageLayout`: webtoon-style chapters made of tall image strips are laid
/// out edge to edge with no chapter title, as in the desktop `_long_image_layout_css`.
/// Writers share these rules so EPUB, HTML, and PDF agree.
public enum LongImageLayout {
    /// Marker class the desktop ntk webtoon pages use.
    public static let pageClass = "ntk-webtoon-page"

    public static let css = """
    body { margin: 0; padding: 0; }
    .ntk-webtoon-page { margin: 0; padding: 0; text-align: center; line-height: 0; }
    .ntk-webtoon-page img, .chapter > img, p > img {
      display: block; width: 100%; max-width: 100%; max-height: none; height: auto;
      margin: 0 auto; padding: 0; page-break-inside: avoid; break-inside: avoid;
    }
    .ntk-webtoon-page + .ntk-webtoon-page { margin-top: 0; }
    """

    /// True when the setting is on and the book looks like image-strip content.
    public static func applies(to bundle: BookBundle) -> Bool {
        guard bundle.settings.images.longImageLayout else { return false }
        return bundle.chapters.contains(where: isImageStrip)
    }

    /// A chapter carrying the webtoon marker, or images with almost no text.
    public static func isImageStrip(_ chapter: ChapterContent) -> Bool {
        if chapter.html.contains(pageClass) { return true }
        guard !chapter.images.isEmpty else { return false }
        let text = HTMLText.plainText(chapter.html).filter { !$0.isWhitespace }
        return text.count < 40
    }

    /// Chapters with images lose their visible title in this layout.
    public static func hidesTitle(_ chapter: ChapterContent, layoutApplies: Bool) -> Bool {
        layoutApplies && !chapter.images.isEmpty
    }
}
