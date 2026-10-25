from scripts.scrape_kpage import (
    add_kpage_item,
    extract_series_detail,
    novel_row_to_dict,
)


def test_catalog_item_uses_native_sale_start_and_leaves_detail_counts_unknown():
    novels = {}
    assert add_kpage_item(novels, {
        "series_id": 64496504,
        "title": "Example",
        "start_sale_dt": "2024-07-01T14:48:21+09:00",
        "service_property": {"view_count": 1418182},
    })
    novel = novels["64496504"]
    assert novel["published"] == "2024-07-01T14:48:21+09:00"
    assert novel["chapters"] is None
    assert novel["likes"] is None
    assert novel["metrics"] == {}


def test_content_overview_extracts_native_chapters_rating_and_description():
    detail = extract_series_detail({"result": {"content": {
        "description": " First line\nSecond line ",
        "start_sale_dt": "2024-07-01T14:48:21+09:00",
        "on_sale_count": 1012,
        "service_property": {
            "view_count": 1418182,
            "rating_count": 16981,
            "rating_sum": 163574,
        },
    }}})
    assert detail == {
        "chapters": 1012,
        "views": 1418182,
        "metrics": {"rating": 9.6, "rating_count": 16981, "rating_scale": 10},
        "description": "First line\nSecond line",
        "published": "2024-07-01T14:48:21+09:00",
    }


def test_zero_rating_count_is_preserved_without_inventing_an_average():
    detail = extract_series_detail({"result": {"content": {
        "on_sale_count": 0,
        "service_property": {"rating_count": 0, "rating_sum": 0},
    }}})
    assert detail["chapters"] == 0
    assert detail["metrics"] == {"rating_count": 0}


def test_compact_row_round_trips_kpage_metrics():
    row = ["7", "Title", "Author", "", [], 10, 0, 12, 0, "2026-10-25", 0, 0,
           "2024-07-01", {"rating": 8.7, "rating_count": 50, "rating_scale": 10}]
    novel = novel_row_to_dict(row)
    assert novel["published"] == "2024-07-01"
    assert novel["chapters"] == 12
    assert novel["metrics"]["rating"] == 8.7
