"""Shared UTF-8 text export for combined and per-request output."""

import os
import re


def write_text_output(anchor_path, entries, separate=False):
    """Write (position, title, text) entries and return the output location.

    Separate output uses the anchor stem as a book directory, with one
    numbered TXT file per successful request directly inside it.
    """
    if not separate:
        with open(anchor_path, 'w', encoding='utf-8') as stream:
            for _position, _title, content in entries:
                stream.write(content)
        return anchor_path

    output_dir = os.path.splitext(anchor_path)[0]
    os.makedirs(output_dir, exist_ok=True)
    for position, title, content in entries:
        safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title or 'Chapter'))
        safe_title = safe_title.strip('. ')[:80].rstrip('. ') or 'Chapter'
        # Numbered headings already identify the chapter; do not prepend a
        # second sequence that counts notices and the selected range.
        numbered_title = re.match(r'^(?:\d+[.)]\s|Chapter \d+$)', safe_title)
        name = safe_title if numbered_title else f'{position:04d} {safe_title}'
        with open(os.path.join(output_dir, name + '.txt'), 'w', encoding='utf-8') as stream:
            stream.write(content)
    return output_dir
