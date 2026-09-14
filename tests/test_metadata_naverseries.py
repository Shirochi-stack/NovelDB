import pytest
from scripts.scrape_naverseries import NaverSeriesAdapter, parse_catalog, BASE
from scripts.metadata_common import FetchError

HTML = '''<ul class="lst_list"><li><a class="pic"><img src="https://example.test/cover.jpg"></a>
<h3><em class="ico n19">19금</em><a href="/novel/detail.series?productNo=123" title="Original title">Original title(3권/완결)</a></h3>
<span class="author">Writer</span><em class="score_num">9.5</em><p class="dsc">Public synopsis preview..</p></li></ul>
<p class="pagenate"><strong>1</strong><a href="/novel/categoryProductList.series?genreCode=201&page=2">2</a></p>'''


def test_explicit_r19_badge_and_series_product_id():
    page = parse_catalog(HTML, {"genre": "201"}, 1)
    assert page.complete and page.next_page == 2
    row = page.records[0]
    assert row["age"] == 19 and row["id"] == "123"
    assert row["title"] == "Original title" and row["complete"] == 1
    assert row["episodes"] is None and row["metrics"]["volumes"] == 3
    assert row["synopsis_is_preview"] and row["synopsis"] == "Public synopsis preview.."
    assert row["canonical_url"] == BASE + "/novel/detail.series?productNo=123"


def test_missing_age_badge_does_not_mean_general_audience():
    row = parse_catalog(HTML.replace('<em class="ico n19">19금</em>', ''), {"genre": "201"}, 1).records[0]
    assert row["age"] is None


def test_wrong_page_and_login_markup_do_not_finish_catalog():
    assert not parse_catalog(HTML, {"genre": "201"}, 2).complete
    assert not parse_catalog('<form>Login</form>', {"genre": "201"}, 1).complete


def test_restricted_details_preserve_catalog_preview():
    class Client:
        def get_text(self, *args, **kwargs):
            raise FetchError("Request destination is outside the metadata allowlist")
    record = parse_catalog(HTML, {"genre": "201"}, 1).records[0]
    result = NaverSeriesAdapter().detail(Client(), record)
    assert result.status == "restricted"
    assert record["age"] == 19 and record["synopsis"] == "Public synopsis preview.."


def test_general_detail_extracts_full_synopsis_and_explicit_age():
    class Client:
        def get_text(self, *args, **kwargs):
            return '<div class="end_head"><h2>Title</h2></div><ul class="end_info"><li>15세 이용가</li></ul><div class="end_dsc"><div class="_synopsis">Short..</div><div class="_synopsis">Full<br>synopsis<span class="al_r">Close</span></div></div>'
    result = NaverSeriesAdapter().detail(Client(), {"id": "123"})
    assert result.status == "success" and result.record["age"] == 15
    assert result.record["synopsis"] == "Full\nsynopsis"
    assert result.record["synopsis_is_preview"] is False


@pytest.mark.parametrize("url", ['https://nid.naver.com/nidlogin.login', BASE+'/novel/viewer.series?productNo=123',
    BASE+'/novel/detail.series?productNo=123&token=x', 'https://user@series.naver.com/novel/detail.series'])
def test_account_and_reader_routes_are_not_requested(url):
    assert not NaverSeriesAdapter.is_allowed_url(url)


def test_detail_base_and_prepared_url_are_allowed():
    assert NaverSeriesAdapter.is_allowed_url(BASE+'/novel/detail.series')
    assert NaverSeriesAdapter.is_allowed_url(BASE+'/novel/detail.series?productNo=123')
