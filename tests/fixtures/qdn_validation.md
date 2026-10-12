The chapter 76 reference contains 94 paragraphs and 2,197 characters,
manually checked against the supplied full-page browser screenshot.
Paragraph indentation is omitted; text and punctuation are retained.

`qdn_chapter76_capture.json` keeps the supplied capture's original font
bytes, character codes, mirror flags, source kinds, and positions. Font
addresses and family labels are replaced with anonymous local identifiers.
Unpainted attribute values remain as decoys in the fixture.

The complete-chapter test reconstructs a browser page from those captured
glyphs. It checks capture and decoding against the screenshot at two zoom
levels, including pseudo-elements, visual ordering, mirrored glyphs,
fallback characters, repeated characters, names, and the distinguishing
left strokes in 借/惜. The original reader's runtime DOM and styles were
not fully preserved in the supplied saved HTML. This replay therefore
does not establish complete live-site capture accuracy.

Live inspection reached the chapter's guest preview. Full live validation
requires an account with access to this chapter and remains deferred.
Exact agreement on this chapter does not establish universal accuracy.

The screenshot reference also supplies a partial exact lookup for the two
unchanged captured font files. All 1,729 encoded character occurrences in
this chapter resolve through 479 observed code/mirror pairs; the remaining
468 characters are literal text. Tests prohibit reference rendering and
shape matching while checking all 94 paragraphs. A font SHA-256 digest,
character code, and mirror state must all match before using an entry.
Unseen pairs and different font files retain shape matching. The original
complete source mapping has not been recovered.
