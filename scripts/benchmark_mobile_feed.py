"""Compare old mobile pagination with the bounded feed using the same local catalog.

Requires Playwright Chromium, psutil and Pillow. Results measure the entire isolated
browser process tree, including workers and the GPU process, rather than JS heap.
Covers are controlled 1600x2400 JPEGs served locally; no accounts or credentials are
used. This is desktop Chromium with mobile emulation, not an Android measurement.
"""
import argparse
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from PIL import Image
import psutil
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.source_names import dumps
MIB=1024**2


class Server(SimpleHTTPRequestHandler):
    baseline=None
    def log_message(self,*args):pass
    def do_GET(self):
        name=urlsplit(self.path).path.lstrip('/') or 'index.html'
        if self.baseline and name in self.baseline:
            body=self.baseline[name]
            self.send_response(200);self.send_header('Content-Type',self.guess_type(name));self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        else:super().do_GET()


class Monitor:
    def __init__(self,pid):
        self.root=psutil.Process(pid);self.samples=[];self.phase='blank';self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        while not self.stop.is_set():
            private=resident=0
            for process in [self.root,*self.root.children(recursive=True)]:
                try:
                    memory=process.memory_full_info();private+=memory.uss;resident+=memory.rss
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            self.samples.append({'phase':self.phase,'privateMiB':private/MIB,'residentMiB':resident/MIB})
            self.stop.wait(.2)
    def recent(self):
        rows=self.samples[-5:]
        return {key:round(statistics.mean(row[key] for row in rows),2) for key in ('privateMiB','residentMiB')}


async def measure(p,base,label,compressed,cover):
    browser=await p.chromium.launch(headless=True)
    cdp=await browser.new_browser_cdp_session()
    pid=next(row['id'] for row in (await cdp.send('SystemInfo.getProcessInfo'))['processInfo'] if row['type']=='browser')
    monitor=Monitor(pid)
    page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True,device_scale_factor=3)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    await page.add_init_script(f"localStorage.setItem('noveldb.lowResCovers','{str(compressed).lower()}');")
    async def route(r):
        host=urlsplit(r.request.url).hostname
        if host in ('127.0.0.1','localhost'):await r.continue_()
        elif host and host.startswith('fonts.'):await r.abort()
        else:await r.fulfill(body=cover,content_type='image/jpeg',headers={'access-control-allow-origin':'*','cache-control':'public,max-age=3600'})
    await page.route('**/*',route)
    try:
        await asyncio.sleep(1.2);blank=monitor.recent();monitor.phase='startup';started=time.perf_counter()
        await page.goto(base,wait_until='domcontentloaded')
        await page.wait_for_function("document.body.dataset.catalogReady==='true' && document.querySelector('.novel-card') && (!document.body.classList.contains('mobile-infinite') || document.querySelector('#results').dataset.feedReady==='true')",timeout=180000)
        ready=time.perf_counter()-started
        await asyncio.sleep(2)
        startup=monitor.recent();peak=max(row['privateMiB'] for row in monitor.samples if row['phase']=='startup')
        feed=await page.locator('body').evaluate('b=>b.classList.contains("mobile-infinite")')
        monitor.phase='browse';max_cards=0
        for index in range(0,60,2):
            if feed:
                selector=f'.novel-card[data-feed-index="{index}"]'
                for _ in range(25):
                    if await page.locator(selector).count():break
                    await page.evaluate('scrollBy({top:450,behavior:"instant"})');await asyncio.sleep(.1)
                await page.locator(selector).scroll_into_view_if_needed(timeout=30000)
                card=page.locator(selector)
            else:
                if index and index%30==0:
                    await page.locator('#paginationBottom .next-page').click()
                    await page.wait_for_function('(number)=>document.querySelector("#paginationTop [aria-current=page]")?.textContent===String(number)',arg=index//30+1)
                card=page.locator('.novel-card').nth(index%30)
                await card.scroll_into_view_if_needed()
            await card.locator('.card-cover.loaded,.card-cover.no-img').wait_for(timeout=30000)
            max_cards=max(max_cards,await page.locator('.novel-card').count())
            await asyncio.sleep(.12)
        monitor.phase='idle';await asyncio.sleep(3)
        earlyIdle=monitor.recent();await asyncio.sleep(12)
        idle=monitor.recent()
        state=await page.evaluate('''()=>({cards:document.querySelectorAll('.novel-card').length,
            covers:document.querySelectorAll('.card-cover.loaded').length,
            decodedCoverMiB:[...document.querySelectorAll('.card-cover.loaded')].reduce((n,c)=>n+(c.naturalWidth||c.width||0)*(c.naturalHeight||c.height||0)*4,0)/1048576,
            catalog:document.querySelector('#resultCount').textContent})''')
        result={'label':label,'compressed':compressed,'browser':browser.version,'mobileEmulation':True,
            'catalogReadySeconds':round(ready,2),'blank':blank,'startup':startup,'startupPeakPrivateMiB':round(peak,2),
            'after60Novels':idle,'after3Seconds':earlyIdle,'maxMountedCards':max_cards,**state,'pageErrors':errors}
        if errors:raise RuntimeError(str(errors))
        print(dumps(result),flush=True)
        return result
    finally:
        monitor.stop.set();monitor.thread.join();await browser.close()


async def main(args):
    names=['index.html','app.js','style.css','cover-save.js','cover-loader.js','source-picker.js']
    baseline={name:subprocess.check_output(['git','show',f'{args.baseline}:docs/{name}'],cwd=ROOT) for name in names}
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Server,directory=str(ROOT/'docs')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}/'
    stream=io.BytesIO();Image.effect_noise((1600,2400),55).convert('RGB').save(stream,format='JPEG',quality=85);cover=stream.getvalue()
    results=[]
    try:
        async with async_playwright() as p:
            for compressed in ([True,False] if args.covers=='both' else [args.covers=='compressed']):
                for repeat in range(args.rounds):
                    for label in (['pagination','feed'] if repeat%2==0 else ['feed','pagination']):
                        Server.baseline=baseline if label=='pagination' else None
                        results.append(await measure(p,base,f'{label}-{repeat+1}',compressed,cover))
    finally:server.shutdown()
    summary=[]
    for compressed in sorted({row['compressed'] for row in results},reverse=True):
        for label in ('pagination','feed'):
            rows=[r for r in results if r['compressed']==compressed and r['label'].startswith(label)]
            summary.append({'mode':label,'compressed':compressed,'runs':len(rows),
                'startupPeakPrivateMiB':round(statistics.median(r['startupPeakPrivateMiB'] for r in rows),2),
                'startupPrivateMiB':round(statistics.median(r['startup']['privateMiB'] for r in rows),2),
                'after60PrivateMiB':round(statistics.median(r['after60Novels']['privateMiB'] for r in rows),2),
                'mountedCards':round(statistics.mean(r['cards'] for r in rows),1),
                'decodedCoverMiB':round(statistics.mean(r['decodedCoverMiB'] for r in rows),2)})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(dumps({'baseline':args.baseline,'method':'Chromium mobile emulation; full browser process-tree private resident RAM; local npia catalog; controlled 1600x2400 JPEG covers; 60 novels browsed per run','summary':summary,'runs':results},indent=2),encoding='utf-8')
    print(dumps({'summary':summary,'report':str(args.output)},indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',default='e77eb612')
    parser.add_argument('--rounds',type=int,default=2)
    parser.add_argument('--covers',choices=['both','compressed','original'],default='both')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts'/'mobile-feed-memory.json')
    asyncio.run(main(parser.parse_args()))
