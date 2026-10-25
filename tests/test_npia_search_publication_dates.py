"""Publication dates use the search field verified against the oldest episode."""
import importlib
import io
import sys
from unittest.mock import patch

import pytest


class Output(io.StringIO):
    def reconfigure(self, **kwargs):
        pass


@pytest.fixture(params=['scripts.scrape_npia', 'scripts.rescrape_npia_noauth'])
def scraper(request):
    with patch.object(sys, 'stdout', Output()):
        return importlib.import_module(request.param)


def test_search_start_date_takes_priority_over_registration_and_updates(scraper):
    # Live search sample; the oldest episode's displayed date was 25.02.04.
    novel = scraper.extract_novel({
        'novel_no': 334848,
        'start_date': '2025-02-04 21:24:15',
        'reg_date': '2025-02-04 12:52:04',
        'last_viewdate': '2026-10-25 16:35:49',
        'created_at': '2020-01-01',
    })
    assert novel['published'] == '2025-02-04T21:24:15'
    assert novel['updated'] == '2026-10-25 16:35:49'


def test_registration_and_latest_update_do_not_invent_a_publication_date(scraper):
    novel = scraper.extract_novel({
        'novel_no': 7, 'start_date': None,
        'reg_date': '2025-02-04 12:52:04',
        'last_viewdate': '2026-10-25 16:35:49',
    })
    assert novel['published'] is None


def test_previous_explicit_publication_fields_remain_supported(scraper):
    novel = scraper.extract_novel({'novel_no': 7, 'novel_regdate': '2020-01-02'})
    assert novel['published'] == '2020-01-02'


def test_duplicate_search_result_can_fill_an_earlier_missing_date(scraper):
    fresh = {}
    scraper.merge_term_results(['first', 'second'], {
        'first': [{'novel_no': 7, 'novel_name': 'Keep this title'}],
        'second': [{'novel_no': 7, 'start_date': '2025-02-04 21:24:15'}],
    }, fresh)
    assert fresh['7']['published'] == '2025-02-04T21:24:15'
    assert fresh['7']['title'] == 'Keep this title'


def test_later_missing_search_date_never_clears_a_known_date(scraper):
    fresh = {}
    scraper.merge_term_results(['first', 'second'], {
        'first': [{'novel_no': 7, 'start_date': '2025-02-04'}],
        'second': [{'novel_no': 7, 'start_date': None}],
    }, fresh)
    assert fresh['7']['published'] == '2025-02-04'
