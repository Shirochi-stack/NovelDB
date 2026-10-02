import numpy as np
import pytest

import floo_image_reader as reader


def test_paragraphs_join_wrapped_lines_and_drop_footer():
    lines = [
        ('　　“救？拿什么救？”', True),
        ('　　仆三那双死灰色的眸子扫过众人，', True),
        ('嘴角勾起一抹嘲弄的弧度。', False),
        ('　　\u98de\u5362提醒您：读书要劳逸结合，', True),
        ('fl_12345678 127.0.0.1', False),
        ('　　支持\u98de\u5362小说网，支持正版阅读', True),
    ]
    assert reader.lines_to_paragraphs(lines) == [
        '“救？拿什么救？”',
        '仆三那双死灰色的眸子扫过众人，嘴角勾起一抹嘲弄的弧度。',
    ]


@pytest.mark.parametrize('noisy', [
    '“这就是创生地？”[072409129\u98de\u5362083493221]',
    '“这就是创生地？”「072409J�\u98de\u536208349322！',
    '“这就是创生地？”l0724O9129\u98de\u5362O83493221I',
])
def test_inline_watermark_is_removed(noisy):
    assert reader.lines_to_paragraphs([(noisy, True)]) == ['“这就是创生地？”']


def test_ads_removed_and_lone_dash_becomes_yi():
    lines = [('他看了—眼（看爽小说，就上\u98de\u5362小说网！）——走了。', True)]
    assert reader.lines_to_paragraphs(lines) == ['他看了一眼——走了。']


def _cell(rows, cols):
    cell = np.zeros((reader.CELL_H, reader.PITCH), np.float32)
    cell[rows, cols] = 1.0
    return cell


def test_bar_reads_yi_and_dash():
    assert reader._LineReader._bar(_cell(slice(26, 28), slice(4, 28))) == '一'
    assert reader._LineReader._bar(_cell(slice(26, 28), slice(0, reader.PITCH))) == '—'


def test_bar_ignores_glyphs_and_underscores():
    glyph = _cell(slice(26, 28), slice(4, 28))
    glyph[10:40, 15:17] = 1.0  # a vertical stroke: 十, not 一
    assert reader._LineReader._bar(glyph) == ''
    assert reader._LineReader._bar(_cell(slice(44, 46), slice(4, 28))) == ''
