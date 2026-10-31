"""Authentic HTTPS+PostgreSQL recovery regressions on synthetic fixtures only."""
# ruff: noqa: F811
import json
import psycopg
from psycopg.rows import dict_row
from playwright.sync_api import expect
import pytest
from test_live_training import live_app, open_question  # noqa: F401
from https_qa_support import https_app, https_browser  # noqa: F401


def login_start(page, qa):
    page.goto(qa['origin']); page.locator('#password').fill(qa['password']); page.locator('#login button').click(); expect(page.locator('#login')).to_be_hidden()
    page.locator('#prepare').click(); expect(page.locator('#start')).to_be_visible(); page.locator('#start').click(); expect(page.locator('#questions article:visible')).to_have_count(1)
    return page.evaluate('paper')


def wait_saved(page):
    page.wait_for_function('paper && serverConfirmed && Object.keys(pending).length===0 && !conflicted')


def test_real_https_offline_refresh_and_reconnect(https_app, https_browser):
    qa= https_app; context=https_browser; page=context.new_page()
    paper=login_start(page,qa); pid=paper['paper_id']; q=next(x for x in paper['questions'] if x['question_type']=='term'); open_question(page,q['id'])
    field=page.locator('#questions textarea:visible'); field.fill('HTTPS保存的合成答案'); wait_saved(page)
    page.evaluate('navigator.serviceWorker.ready');page.wait_for_function('navigator.serviceWorker.controller!==null')
    context.set_offline(True);field.fill('HTTPS离线刷新后必须保留');page.reload()
    expect(page.locator('#questions article:visible')).to_have_count(1)
    expect(page.locator('#questions textarea:visible')).to_have_value('HTTPS离线刷新后必须保留')
    expect(page.locator('#status')).to_contain_text('服务端状态待确认')
    assert page.evaluate('serverConfirmed') is False
    assert page.evaluate('paper.timing.deadline')==paper['timing']['deadline']
    context.set_offline(False);page.evaluate('reconnect()');wait_saved(page)
    with psycopg.connect(qa['dsn'],row_factory=dict_row) as c:
        row=c.execute('SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s',(pid,q['id'])).fetchone()
        assert row['value']['text']=='HTTPS离线刷新后必须保留'
        assert c.execute('SELECT deadline FROM training_sessions WHERE paper_id=%s',(pid,)).fetchone()['deadline'].isoformat().replace('+00:00','Z') == paper['timing']['deadline'].replace('+00:00','Z')
    page.close()


@pytest.fixture
def firefox_https(https_app):
    from playwright.sync_api import sync_playwright
    from https_qa_support import trusted_browser
    with sync_playwright() as pw:
        context=trusted_browser(pw,'firefox',https_app['root'])
        try: yield context
        finally: context.close()


def row(qa,query,args=()):
    with psycopg.connect(qa['dsn'],row_factory=dict_row) as c:
        return c.execute(query,args).fetchone()



@pytest.mark.parametrize('identity_result',['cached_200','network_failure'])
def test_firefox_identity_does_not_prove_paper_reachability(https_app,firefox_https,identity_result):
    qa=https_app;page=firefox_https.new_page();paper=login_start(page,qa)
    q=next(x for x in paper['questions'] if x['question_type']=='term');open_question(page,q['id'])
    page.locator('#questions textarea:visible').fill('身份与试卷接口故障回归');wait_saved(page)
    # Actual HTTPS origin and database; transport fault explicitly reproduces cached identity / partial outage.
    if identity_result=='cached_200':
        page.route('**/auth/me',lambda route:route.fulfill(json={'username':'test-owner'}))
    else:
        page.route('**/auth/me',lambda route:route.abort('internetdisconnected'))
    page.route('**/v2/papers/*',lambda route:route.abort('internetdisconnected'))
    page.reload()
    expect(page.locator('#questions article:visible')).to_have_count(1)
    expect(page.locator('#questions textarea:visible')).to_have_value('身份与试卷接口故障回归')
    expect(page.locator('#status')).to_contain_text('服务端状态待确认')
    assert page.evaluate('navigator.onLine') is True  # Never depend on the OS flag to authorize synchronization.
    assert page.evaluate('serverConfirmed') is False
    page.locator('#questions textarea:visible').fill('本机暂存未假称同步成功')
    assert row(qa,'SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s',(paper['paper_id'],q['id']))['value']['text']=='身份与试卷接口故障回归'
    page.unroute('**/auth/me');page.unroute('**/v2/papers/*');page.evaluate('reconnect()');wait_saved(page)
    assert row(qa,'SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s',(paper['paper_id'],q['id']))['value']['text']=='本机暂存未假称同步成功'


@pytest.mark.parametrize('endpoint,code',[('auth',401),('auth',403),('paper',401),('paper',403)])
def test_authorization_rejection_locks_but_preserves_valid_draft(https_app,firefox_https,endpoint,code):
    qa=https_app;page=firefox_https.new_page();paper=login_start(page,qa)
    q=next(x for x in paper['questions'] if x['question_type']=='term');open_question(page,q['id'])
    page.evaluate("serverConfirmed=false")
    page.locator('#questions textarea:visible').fill('授权失败时保留有效本机草稿')
    pattern='**/auth/me' if endpoint=='auth' else '**/v2/papers/*'
    page.route(pattern,lambda route:route.fulfill(status=code,json={'detail':'Synthetic authorization rejection'}))
    page.reload();expect(page.locator('#login')).to_be_visible();expect(page.locator('#questions')).to_be_hidden()
    saved=page.evaluate("JSON.parse(localStorage.getItem(sessionStorage.getItem('quiz-v2-paper-key')))")
    assert saved['pending'][str(q['id'])]['text']=='授权失败时保留有效本机草稿'
    assert page.locator('#questions article').count()==0
    # A later network failure cannot silently turn a rejected session into offline edit permission.
    page.unroute(pattern);page.route('**/auth/me',lambda route:route.abort('internetdisconnected'))
    page.reload();expect(page.locator('#login')).to_be_visible();assert page.locator('#questions article').count()==0


@pytest.mark.parametrize('bad_cache',['other_user','bound_username','bad_json','bad_version','extended_deadline'])
def test_invalid_or_foreign_cache_never_becomes_editable(https_app,firefox_https,bad_cache):
    qa=https_app;page=firefox_https.new_page();login_start(page,qa)
    page.evaluate("""kind => {
      const pointer=sessionStorage.getItem('quiz-v2-paper-key');const saved=JSON.parse(localStorage.getItem(pointer));
      if(kind==='other_user')sessionStorage.setItem('quiz-v2-user','different-qa-user');
      if(kind==='bound_username')saved.username='different-qa-user';
      if(kind==='bad_version')saved.version=-1;
      if(kind==='extended_deadline')saved.paper.timing.deadline=new Date(Date.parse(saved.paper.timing.deadline)+3600000).toISOString();
      if(kind!=='other_user')localStorage.setItem(pointer,kind==='bad_json'?'{broken-json':JSON.stringify(saved));
    }""",bad_cache)
    page.route('**/auth/me',lambda route:route.abort('internetdisconnected'));page.route('**/v2/papers/*',lambda route:route.abort('internetdisconnected'));page.reload()
    expect(page.locator('#status')).to_contain_text('其他用户' if bad_cache in ('other_user','bound_username') else '缓存')
    expect(page.locator('#questions')).to_be_hidden();assert page.locator('#questions article').count()==0
    assert page.evaluate("localStorage.getItem(sessionStorage.getItem('quiz-v2-paper-key'))!==null")


@pytest.mark.parametrize('terminal',['not_started','submitted'])
def test_cached_terminal_status_never_unlocks_offline(https_app,firefox_https,terminal):
    qa=https_app;page=firefox_https.new_page()
    if terminal=='submitted':
        paper=login_start(page,qa);pid=paper['paper_id']
        page.locator('#submit').click();page.locator('#confirm-submit').click();expect(page.locator('#result > h2')).to_be_visible()
        assert row(qa,'SELECT submitted_at FROM training_sessions WHERE paper_id=%s',(pid,))['submitted_at']
    else:
        page.goto(qa['origin']);page.locator('#password').fill(qa['password']);page.locator('#login button').click();expect(page.locator('#login')).to_be_hidden();page.locator('#prepare').click();expect(page.locator('#start')).to_be_visible()
        assert page.locator('#questions article').count()==0
    page.route('**/auth/me',lambda route:route.fulfill(json={'username':'test-owner'}));page.route('**/v2/papers/*',lambda route:route.abort('internetdisconnected'))
    page.reload();expect(page.locator('#status')).to_contain_text('尚未开始' if terminal=='not_started' else '已提交')
    expect(page.locator('#questions')).to_be_hidden();assert page.locator('#questions article').count()==0
    assert page.evaluate('paper.timing.status')==terminal


def test_identity_change_during_reconnect_preserves_old_user_cache(https_app,firefox_https):
    qa=https_app;page=firefox_https.new_page();paper=login_start(page,qa)
    q=next(x for x in paper['questions'] if x['question_type']=='term');open_question(page,q['id']);page.evaluate('serverConfirmed=false');page.locator('#questions textarea:visible').fill('原QA用户的本机答案')
    pointer=page.evaluate("sessionStorage.getItem('quiz-v2-paper-key')")
    page.route('**/auth/me',lambda route:route.fulfill(json={'username':'different-qa-user'}));page.evaluate('reconnect()')
    expect(page.locator('#questions')).to_be_hidden();assert page.locator('#questions article').count()==0
    saved=page.evaluate('(pointer)=>JSON.parse(localStorage.getItem(pointer))',pointer)
    assert saved['username']=='test-owner' and saved['pending'][str(q['id'])]['text']=='原QA用户的本机答案'


def test_reconnect_conflict_does_not_overwrite_remote_answers(https_app,firefox_https):
    qa=https_app;context=firefox_https;page=context.new_page();paper=login_start(page,qa);pid=paper['paper_id']
    q=next(x for x in paper['questions'] if x['question_type']=='term');open_question(page,q['id'])
    page.locator('#questions textarea:visible').fill('原服务端答案');wait_saved(page);page.evaluate('navigator.serviceWorker.ready');page.wait_for_function('navigator.serviceWorker.controller!==null')
    context.set_offline(True);page.locator('#questions textarea:visible').fill('本机离线新答案')
    fresh=qa['client'].get(f'/v2/papers/{pid}').json()
    assert qa['client'].put(f'/v2/papers/{pid}/drafts/{q["id"]}',json={'expected_version':fresh['timing']['version'],'value':{'text':'另一设备较新答案'}}).status_code==200
    page.reload();expect(page.locator('#questions textarea:visible')).to_have_value('本机离线新答案')
    context.set_offline(False);page.evaluate('reconnect()');expect(page.locator('#conflict')).to_be_visible()
    assert row(qa,'SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s',(pid,q['id']))['value']['text']=='另一设备较新答案'
    saved=page.evaluate('JSON.parse(localStorage.getItem(key()))');assert saved['pending'][str(q['id'])]['text']=='本机离线新答案'


def test_queued_photos_survive_reload_and_lost_receipt_without_duplicates(https_app,firefox_https):
    import hashlib,io
    from PIL import Image
    qa=https_app;context=firefox_https;page=context.new_page();paper=login_start(page,qa);pid=paper['paper_id']
    q=next(x for x in paper['questions'] if x['question_type']=='term');qid=q['id'];open_question(page,qid)
    def photo(color):
        b=io.BytesIO();Image.new('RGB',(48,32),color).save(b,format='PNG');return b.getvalue()
    a,b=photo('red'),photo('blue');pattern=f'**/images/{qid}?*'
    def lost_receipt(route):
        response=qa['client'].post(route.request.url,content=route.request.post_data_buffer,headers={'Content-Type':'image/png'});assert response.status_code==200;route.abort('internetdisconnected')
    page.route(pattern,lost_receipt)
    page.locator('#questions article:visible input[type=file]').nth(1).set_input_files({'name':'lost.png','mimeType':'image/png','buffer':a})
    page.wait_for_function("document.querySelector('#status').textContent.includes('照片原件仍保留')")
    assert row(qa,'SELECT count(*) AS n FROM answer_images WHERE paper_id=%s',(pid,))['n']==1
    page.unroute(pattern);page.evaluate('navigator.serviceWorker.ready');page.wait_for_function('navigator.serviceWorker.controller!==null');context.set_offline(True)
    page.locator('#questions article:visible input[type=file]').nth(1).set_input_files({'name':'offline.png','mimeType':'image/png','buffer':b})
    page.wait_for_function("photoStore('getAll').then(rows=>rows.length===2)")
    page.reload();expect(page.locator('#questions article:visible')).to_have_count(1)
    assert page.evaluate("photoStore('getAll').then(rows=>rows.length)")==2
    context.set_offline(False);page.evaluate('reconnect()');page.wait_for_function("photoStore('getAll').then(rows=>rows.length===0)")
    page.evaluate('reconnect()')
    with psycopg.connect(qa['dsn'],row_factory=dict_row) as c:
        images=c.execute('SELECT content,sha256 FROM answer_images WHERE paper_id=%s',(pid,)).fetchall();assert len(images)==2
        assert {hashlib.sha256(bytes(x['content'])).hexdigest() for x in images}=={hashlib.sha256(a).hexdigest(),hashlib.sha256(b).hexdigest()}
        ids=c.execute('SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s',(pid,qid)).fetchone()['value']['image_ids'];assert len(ids)==len(set(ids))==2


def test_offline_expiry_keeps_deadline_and_rejects_late_answers(https_app,firefox_https):
    from app.training_spec import default_blueprint
    qa=https_app;context=firefox_https
    p=qa['client'].post('/v2/papers',json=default_blueprint()).json();pid=p['paper_id'];p=qa['client'].post(f'/v2/papers/{pid}/start').json();q=next(x for x in p['questions'] if x['question_type']=='term');qid=q['id']
    assert qa['client'].put(f'/v2/papers/{pid}/drafts/{qid}',json={'expected_version':0,'value':{'text':'截止前服务端答案'}}).status_code==200
    # Only this disposable fixture is made near-expiry; keep its exact 3600-second invariant.
    with psycopg.connect(qa['dsn']) as c:
        c.execute("UPDATE training_sessions SET started_at=now()-interval '3593 seconds',deadline=now()+interval '7 seconds' WHERE paper_id=%s",(pid,))
    page=context.new_page();page.goto(qa['origin']);page.locator('#password').fill(qa['password']);page.locator('#login button').click();expect(page.locator('#login')).to_be_hidden();page.locator('#prepare').click();expect(page.locator('#questions article:visible')).to_have_count(1);open_question(page,qid)
    deadline=page.evaluate('paper.timing.deadline');page.evaluate('navigator.serviceWorker.ready');page.wait_for_function('navigator.serviceWorker.controller!==null');context.set_offline(True)
    page.locator('#questions textarea:visible').fill('离线未同步答案');page.wait_for_function('estimatedNow()>=deadline',timeout=15000)
    page.wait_for_function('document.querySelector("#questions").inert',timeout=2000)
    assert page.locator('#questions').evaluate('node=>node.inert')
    page.evaluate('(qid)=>change(qid,{text:"截止后的迟到答案"})',qid)
    assert page.evaluate('(qid)=>pending[qid].text',qid)=='离线未同步答案'
    page.reload();expect(page.locator('#questions article:visible')).to_have_count(1);assert page.evaluate('paper.timing.deadline')==deadline;page.wait_for_function('document.querySelector("#questions").inert',timeout=2000);assert page.locator('#questions').evaluate('node=>node.inert'), page.evaluate('({now:estimatedNow(),deadline,confirmed:serverConfirmed,status:document.querySelector("#status").textContent,server_now:paper.timing.server_now})')
    context.set_offline(False);page.evaluate('reconnect()');expect(page.locator('#result > h2')).to_be_visible()
    with psycopg.connect(qa['dsn'],row_factory=dict_row) as c:
        value=c.execute('SELECT a.answer FROM answers_v2 a JOIN attempts_v2 t ON a.attempt_id=t.id WHERE t.paper_id=%s AND a.question_id=%s',(pid,qid)).fetchone()['answer'];assert json.loads(value)['text']=='截止前服务端答案'
    assert page.evaluate('(qid)=>JSON.parse(localStorage.getItem(key())).pending[qid].text',qid)=='离线未同步答案'


def test_new_tab_without_session_pointer_preserves_unsynced_local_answers(https_app,firefox_https):
    qa=https_app;context=firefox_https;page=context.new_page();paper=login_start(page,qa);pid=paper['paper_id']
    q=next(x for x in paper['questions'] if x['question_type']=='term');open_question(page,q['id'])
    page.route('**/drafts/*',lambda route:route.abort('internetdisconnected'))
    page.locator('#questions textarea:visible').fill('关闭标签页前尚未同步的有效草稿')
    page.wait_for_function('serverConfirmed===false && Object.keys(pending).length===1');page.close()
    next_page=context.new_page();next_page.goto(qa['origin']);next_page.wait_for_function('startup===null')
    assert next_page.evaluate("sessionStorage.getItem('quiz-v2-paper-key')")==None
    next_page.locator('#prepare').click();expect(next_page.locator('#questions article:visible')).to_have_count(1);wait_saved(next_page)
    assert row(qa,'SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s',(pid,q['id']))['value']['text']=='关闭标签页前尚未同步的有效草稿'
    open_question(next_page,q['id']);expect(next_page.locator('#questions textarea:visible')).to_have_value('关闭标签页前尚未同步的有效草稿')


def test_v7_cache_storage_migrates_to_v8_and_offline_refresh(https_app, https_browser):
    """A browser with old v7 shell caches obtains v8 assets before an offline reload.

    The prior CacheStorage payload is deliberately marked stale. It cannot be used
    to pass this test: after activation only the new cache can serve offline.
    Uses real HTTPS, real service worker activation, and disposable PostgreSQL.
    """
    qa = https_app
    context = https_browser
    page = context.new_page()
    page.goto(qa['origin'] + '/static/model.html')
    page.evaluate("""async () => {
      const old = await caches.open('xiaohan-training-shell-v7-offline-recovery');
      await old.put('/static/training.js', new Response(
        'window.__staleV7Training = true;', {
          headers: {'Content-Type': 'application/javascript'}
        }
      ));
      await old.put('/', new Response(
        '<!doctype html><title>obsolete v7 shell</title>', {
          headers: {'Content-Type': 'text/html'}
        }
      ));
    }""")
    assert page.evaluate(
        "async () => (await caches.keys()).includes('xiaohan-training-shell-v7-offline-recovery')"
    )
    paper = login_start(page, qa)
    q = next(x for x in paper['questions'] if x['question_type'] == 'term')
    open_question(page, q['id'])
    field = page.locator('#questions textarea:visible')
    field.fill('v7缓存升级前的合成草稿')
    wait_saved(page)
    page.evaluate('navigator.serviceWorker.ready')
    page.wait_for_function('navigator.serviceWorker.controller !== null')
    page.wait_for_function("""async () => {
      const keys = await caches.keys();
      return keys.includes('xiaohan-training-shell-v9-sixty-question-upgrade') &&
             !keys.includes('xiaohan-training-shell-v7-offline-recovery');
    }""")
    assert page.evaluate("""async () => {
      const cache = await caches.open('xiaohan-training-shell-v9-sixty-question-upgrade');
      const asset = await cache.match('/static/training.js');
      return !!asset && (await asset.text()).includes('cachedPaper(username, paper.paper_id)');
    }""")
    context.set_offline(True)
    field.fill('v8升级后离线刷新保留的草稿')
    page.reload()
    expect(page.locator('#questions article:visible')).to_have_count(1)
    expect(page.locator('#questions textarea:visible')).to_have_value('v8升级后离线刷新保留的草稿')
    assert page.evaluate('serverConfirmed') is False
    assert page.evaluate('paper.timing.deadline') == paper['timing']['deadline']
    assert page.evaluate('typeof window.__staleV7Training') == 'undefined'
    context.set_offline(False)
    page.evaluate('reconnect()')
    wait_saved(page)
    assert row(qa, 'SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s',
               (paper['paper_id'], q['id']))['value']['text'] == 'v8升级后离线刷新保留的草稿'
    page.close()
