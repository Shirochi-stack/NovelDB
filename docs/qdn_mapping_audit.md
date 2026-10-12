# qdn mapping audit

The supplied archive does not contain enough reader code to recover the
complete original character mapping. This review used saved material only;
it did not execute the attached parser or obfuscated scripts.

## Saved reader evidence

The saved reader HTML references 13 module bundles, including
`src_pages_chapter_index.page.3efd899e.js` and
`renderer_default.page.client.d16f060d.js`. None of these bundles is present
in the archive. The available scripts include an encryption helper exposing
an unlock operation, but its reader caller is missing. Consequently, the
samples cannot establish whether the unlock result includes readable text
or still requires font decoding.

The hydration response contains encrypted chapter content (38,248 decoded
bytes with a `U40:` prefix), plain CSS ordering rules, and a binary font
represented as a JSON byte array. The captured fonts have generic glyph
names and scrambled character codes. Their Unicode mapping tables do not
supply the original character identities. No complete semantic mapping was
found in the inspected responses or font metadata. Account and key material
is excluded from this report and from the generated mapping.

## Verified partial lookup

`data/qdn_verified_maps.json` contains 479 observed character-code/mirror
pairs for two exact captured font files. These pairs were built by aligning
the painted captured character stream with the manually checked chapter 76
screenshot reference, after excluding unpainted attribute decoys. The
builder rejects unequal paragraph lengths, changed literal text, and
conflicting assignments; it does not invoke shape matching.

The lookup covers all 1,729 encoded glyph occurrences in the reference's
94 paragraphs (2,197 characters total). The other 468 characters pass
through literally. Decoding this capture therefore needs no shape matching.
This is a chapter-backed partial mapping, not the recovered original table.

Each lookup is bound to the SHA-256 digest of the complete font bytes and
the captured character code and mirror state. Unknown combinations use the
existing shape matcher and its uncertainty diagnostics. Changed font bytes
cannot inherit these entries. Statistics and chapter logs distinguish
verified lookups from shape matching. Both full application packages include
the optional mapping file.

Run `python scripts/build_qdn_verified_map.py` to reproduce the lookup from
the checked fixtures. The regression suite checks deterministic generation,
complete chapter agreement with shape matching disabled, and fallback for
unseen codes, changed fonts, mirror states, and a corrupt optional map.

Recovering the full reader transformation would require the missing reader
bundles and an accessible response from the same reader version. Until that
is available, these samples cannot support a universal exact decoder.
