import pytest

from rbooks_app_proxy import RbooksAppProxy, RbooksAppError
from scripts.source_names import dumps


@pytest.fixture(scope='module')
def library_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def card(page, book_id='2', title='Series 2권', **flags):
    page.evaluate("""data => {
      window.actions = window.actions || [];
      const image = document.createElement('img');
      image.style.cssText = 'width:100px;height:150px';
      const props = {
        book:{bId:data.book_id,title:data.title,layout:{type:'oneVolume'}},
        isDownloaded:false,isDownloading:false,isCurrentDownloading:false,
        ...data.flags
      };
      image.__reactFiberTest = {memoizedProps:props,return:null};
      image.onclick = () => window.actions.push(props.book.bId);
      document.body.appendChild(image);
    }""", {'book_id':book_id, 'title':title, 'flags':flags})


def inspect(page, action='observe'):
    return page.evaluate(RbooksAppProxy.LIBRARY_VOLUME_SCRIPT % tuple(
        dumps(value) for value in ('2', 'Series 2권', 'Series', action)))


def test_library_clicks_exact_volume_cover(library_page):
    library_page.set_content('<p>Series</p>')
    card(library_page, '1', 'Series 1권', isDownloaded=True)
    card(library_page, '2', 'Series 2권', isDownloaded=True)
    result = inspect(library_page, 'activate')
    assert result['action'] == 'opened'
    assert result['bookId'] == '2'
    assert library_page.evaluate('window.actions') == ['2']


@pytest.mark.parametrize('current', [False, True])
def test_library_never_cancels_queued_or_downloading_volume(library_page, current):
    library_page.set_content('')
    library_page.evaluate('window.actions=[]')
    card(library_page, isDownloading=True, isCurrentDownloading=current,
         downloadingProgress=0.37)
    result = inspect(library_page, 'activate')
    assert result['status'] == ('downloading' if current else 'queued')
    assert result['percent'] == (37 if current else None)
    assert 'action' not in result
    assert library_page.evaluate('window.actions') == []


def test_library_starts_missing_download(library_page):
    library_page.set_content('')
    library_page.evaluate('window.actions=[]')
    card(library_page)
    assert inspect(library_page, 'activate')['action'] == 'download-requested'
    assert library_page.evaluate('window.actions') == ['2']


def test_library_expands_group_even_when_cover_belongs_to_first_volume(library_page):
    library_page.set_content('')
    library_page.evaluate('window.actions=[]')
    card(library_page, '1', 'Series', book={
        'bId':'1','title':'Series','unitId':123,
        'layout':{'type':'manyVolume','totalVolume':3}})
    assert inspect(library_page)['status'] == 'group'
    assert not library_page.evaluate('window.actions')
    assert inspect(library_page, 'expand')['seriesId'] == '123'
    assert library_page.evaluate('window.actions') == ['1']


def test_library_rejects_other_volume_with_matching_title(library_page):
    library_page.set_content('')
    library_page.evaluate('window.actions=[]')
    card(library_page, 'other', 'Series 2권')
    assert inspect(library_page, 'activate')['status'] == 'missing'
    assert library_page.evaluate('window.actions') == []


def test_library_ignores_unrelated_progress(library_page):
    library_page.set_content('')
    card(library_page, '1', 'Series 1권', isDownloading=True,
         isCurrentDownloading=True, downloadingProgress=0.81)
    card(library_page, isDownloading=True, isCurrentDownloading=False,
         downloadingProgress=0.81)
    assert inspect(library_page)['percent'] is None
    assert inspect(library_page)['status'] == 'queued'


def test_series_expands_once_while_its_volumes_load(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    states = iter([{'status':'group','seriesId':'series'},
                   {'status':'group','seriesId':'series'}, {'status':'ready'}])
    expanded = []

    def state(book_id, title, action='observe'):
        if action == 'expand':
            expanded.append(book_id)
            return {'status':'group'}
        return next(states)

    monkeypatch.setattr(proxy, '_library_volume_state', state)
    def wait(check, *args):
        assert check() is None
        assert check() is None
        return check()
    monkeypatch.setattr(proxy, '_wait', wait)
    assert proxy._locate_owned_volume('2', 'Series 2권')['status'] == 'ready'
    assert expanded == ['2']


def test_reader_waits_for_download_and_opens_without_cancelling(monkeypatch):
    import rbooks_app_proxy
    logs = []
    proxy = RbooksAppProxy(logs.append)
    states = iter([{'status':'queued', 'bookId':'2'},
                   {'status':'downloading', 'bookId':'2','percent':20},
                   {'status':'downloading', 'bookId':'2','percent':80},
                   {'status':'downloaded', 'bookId':'2'}])
    now = [0]
    opened = []
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(rbooks_app_proxy.time, 'sleep',
                        lambda delay: now.__setitem__(0, now[0]+1))
    monkeypatch.setattr(proxy, '_tab',
                        lambda suffix: {'id':'requested'} if opened else None)
    monkeypatch.setattr(proxy, '_evaluate_target', lambda *args: True)
    monkeypatch.setattr(proxy, '_reader_cache_error', lambda snapshot: False)
    proxy._wait_for_viewer(
        None, title='Series 2권',
        reopen=lambda: opened.append(True) or {'action':'opened'},
        download_status=lambda: next(states, {'status':'missing','bookId':'2'}))
    assert opened == [True]
    assert any('queued' in line for line in logs)
    assert any('downloading (20%)' in line for line in logs)
    assert proxy._reader_targets['Viewer'] == 'requested'


def test_progress_extends_wait_but_stalled_volume_times_out(monkeypatch):
    import rbooks_app_proxy
    proxy = RbooksAppProxy(lambda line: None)
    now = [0]
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(rbooks_app_proxy.time, 'sleep',
                        lambda delay: now.__setitem__(0, now[0]+100))
    monkeypatch.setattr(proxy, '_tab', lambda suffix: None)
    monkeypatch.setattr(proxy, '_reader_cache_error', lambda snapshot: False)
    states = iter([{'status':'downloading','bookId':'2','percent':10},
                   {'status':'downloading','bookId':'2','percent':20},
                   {'status':'downloading','bookId':'2','percent':30}])
    with pytest.raises(RbooksAppError, match='stopped making progress'):
        proxy._wait_for_viewer(
            None, title='Series 2권',
            download_status=lambda: next(states, {
                'status':'downloading','bookId':'2','percent':30}))
    assert now[0] == 400


def test_volume_switch_reuses_authorized_session_and_closes_previous_reader(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    context = object()
    proxy._authorized_context = context
    calls = []
    monkeypatch.setattr(proxy, '_close_reader_windows', lambda: calls.append('close'))
    monkeypatch.setattr(proxy, '_authorize_library',
                        lambda context: pytest.fail('Repeated the sign-in handoff'))
    monkeypatch.setattr(proxy, '_tab', lambda suffix: {'id':'library'})
    monkeypatch.setattr(proxy, '_select_purchase_tab',
                        lambda: calls.append('purchase-tab') or True)
    monkeypatch.setattr(proxy, '_locate_owned_volume',
                        lambda *args: calls.append('exact-volume'))
    monkeypatch.setattr(proxy, '_reader_log_snapshot', lambda: None)
    monkeypatch.setattr(proxy, '_wait', lambda check, *args: check())
    monkeypatch.setattr(proxy, '_wait_for_viewer',
                        lambda *args, **kwargs: calls.append('open'))
    proxy._open_owned_book(context, '2', 'Series 2권')
    assert calls == ['close','purchase-tab','exact-volume','open']


def test_library_refresh_during_open_expands_series_again(monkeypatch):
    import rbooks_app_proxy
    proxy = RbooksAppProxy(lambda line: None)
    states = iter([{'status':'group','bookId':'2'},
                   {'status':'ready','bookId':'2'},
                   {'status':'downloading','bookId':'2','percent':50},
                   {'status':'downloaded','bookId':'2'}])
    actions = []
    now = [0]
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(rbooks_app_proxy.time, 'sleep',
                        lambda delay: now.__setitem__(0, now[0]+1))
    monkeypatch.setattr(proxy, '_tab', lambda suffix:
                        {'id':'volume-2'} if actions[-1:] == ['opened'] else None)
    monkeypatch.setattr(proxy, '_evaluate_target', lambda *args: True)
    monkeypatch.setattr(proxy, '_reader_cache_error', lambda snapshot: False)
    next_actions = iter(['expanded','download-requested','opened'])
    def reopen():
        action = next(next_actions)
        actions.append(action)
        return {'action':action}
    proxy._wait_for_viewer(
        None, title='Series 2권', reopen=reopen,
        download_status=lambda: next(states, {'status':'missing','bookId':'2'}))
    assert actions == ['expanded','download-requested','opened']
    assert now[0] < 10


def test_reader_target_replacement_is_retryable(monkeypatch):
    import rbooks_app_proxy
    proxy = RbooksAppProxy(lambda line: None)
    tabs = iter([{'id':'retired'}, {'id':'new-reader'}])
    monkeypatch.setattr(proxy, '_tab', lambda suffix: next(tabs))
    monkeypatch.setattr(proxy, '_reader_cache_error', lambda snapshot: False)
    monkeypatch.setattr(rbooks_app_proxy.time, 'sleep', lambda delay: None)
    def evaluate(tab, expression):
        if tab['id'] == 'retired':
            raise RuntimeError('Handshake status 500: No such target id: retired')
        return True
    monkeypatch.setattr(proxy, '_evaluate_target', evaluate)
    proxy._wait_for_viewer(None, title='Requested volume')
    assert proxy._reader_targets['Viewer'] == 'new-reader'


def test_library_target_replacement_returns_missing_without_masking_real_errors(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    monkeypatch.setattr(proxy, '_tab', lambda suffix: {'id':'old-library'})
    def gone(*args):
        raise RuntimeError('No such target id: old-library')
    monkeypatch.setattr(proxy, '_evaluate_target', gone)
    assert proxy._library_volume_state('2', 'Series 2권')['status'] == 'missing'
    def broken(*args):
        raise RbooksAppError('UI command failed')
    monkeypatch.setattr(proxy, '_evaluate_target', broken)
    with pytest.raises(RbooksAppError, match='UI command failed'):
        proxy._library_volume_state('2', 'Series 2권')
