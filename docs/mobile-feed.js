/* Responsive feed: a bounded card window over lazily requested catalog slices. */
(function () {
    'use strict';
    window.NovelMobileFeed = class {
        constructor({host,controls,load,card,mounted,activity}) {
            Object.assign(this,{host,controls,load,card,mounted,activity});
            this.size=30;this.columns=2;this.enabled=false;this.epoch=0;this.frame=0;this.cache=new Map();this.pending=new Map();
            this.heights=[];this.prefix=[0];this.estimate=660;this.extent=0;this.total=0;this.identity=null;
            this.handoff=new Map();this.observed=new Set();
            this.top=document.createElement('div');this.top.className='mobile-feed-space';this.top.setAttribute('aria-hidden','true');
            this.grid=document.createElement('div');this.grid.className='mobile-feed-grid';
            this.bottom=this.top.cloneNode();
            this.status=document.createElement('button');this.status.type='button';this.status.className='mobile-feed-status';this.status.setAttribute('aria-live','polite');
            this.status.addEventListener('click',()=>{this.failed=null;this.schedule();});
            this.placeholder=document.createElement('div');this.placeholder.hidden=true;controls.before(this.placeholder);
            this.lastY=scrollY;this.direction=0;this.lastScrollAt=0;this.settleUntil=0;this.keep=null;this.kept=null;this.jumpOffset=0;
            window.addEventListener('scroll',()=>{if(!this.enabled)return;this.activity?.();this.menu();this.schedule();},{passive:true});
            // A zoom or a resize re-lays out every row. The card at the top of the view stays where it is, and the
            // scrolls the re-layout causes neither move the list nor count as a gesture for the floating menu.
            window.addEventListener('resize',()=>{if(this.enabled&&!this.keep)this.keep=this.anchor();this.layout=null;this.heights=[];this.dirty=true;this.settle();this.rebuild();this.schedule();});
            document.addEventListener('close',()=>this.schedule(),true);
            // Each card reports its own size after layout, so scrolling never forces a layout to measure rows.
            this.resize=new ResizeObserver(entries=>{
                for(const entry of entries)entry.target._feedHeight=entry.borderBoxSize?.[0]?.blockSize??entry.target.getBoundingClientRect().height;
                this.measure();
            });
        }
        setEnabled(enabled) {
            if(this.enabled===enabled)return;
            this.enabled=enabled;this.epoch++;this.identity=null;this.cache.clear();this.pending.clear();this.heights=[];this.prefix=[0];this.extent=0;
            this.layout=null;this.lastY=scrollY;this.direction=0;
            this.floatMenu(false);this.host.classList.toggle('mobile-feed',enabled);
            document.body.classList.toggle('mobile-infinite',enabled);
            const cards=[...this.host.querySelectorAll('.novel-card')];
            if(enabled){this.columns=this.columnCount();this.grid.replaceChildren(...cards);this.host.replaceChildren(this.top,this.grid,this.bottom,this.status);}
            else {this.host.replaceChildren(...cards);this.grid.replaceChildren();this.handoff.clear();this.unobserve();}
        }
        invalidate() {this.epoch++;this.identity=null;this.busy=false;this.pending.clear();}
        // The row at the top of the view (its first card) and how far above the view its top is, from the
        // layout before the resize: by the time the event fires the cards have already reflowed. None while
        // the feed starts below the top of the view (the reader is at the controls, so nothing should move).
        anchor() {
            const local=-this.host.getBoundingClientRect().top;
            if(local<=0||this.prefix.length<2)return null;
            const row=this.rowAt(local);
            // Zooming in steps keeps the same card, not the first card of each new row, until the reader scrolls.
            if(this.kept==null||Math.floor(this.kept/this.columns)!==row)this.kept=row*this.columns;
            return {index:this.kept,offset:(this.prefix[row]||0)-local};
        }
        settle() {this.settleUntil=performance.now()+500;this.lastY=Math.max(0,scrollY);this.direction=0;}
        columnCount() {return Math.max(1,parseInt(getComputedStyle(this.host).getPropertyValue('--feed-columns'),10)||2);}
        // Breakpoint values change only with the viewport; reading styles every frame forces a style recalculation.
        metrics() {
            if(!this.layout)this.layout={columns:this.columnCount(),gap:parseFloat(getComputedStyle(this.grid).rowGap)||0,overscan:matchMedia('(max-width: 650px)').matches?2:1};
            return this.layout;
        }
        unobserve(keep) {
            for(const card of this.observed)if(!keep?.has(card)){this.resize.unobserve(card);this.observed.delete(card);delete card._feedHeight;}
        }
        jumpTo(page) {this.jumpRow=Math.floor((page-1)*this.size/this.columns);this.dirty=true;this.schedule();}
        reset(identity) {
            this.epoch++;this.identity=identity;this.cache.clear();this.pending.clear();this.failed=null;
            this.heights=[];this.prefix=[0];this.extent=0;this.total=0;this.start=0;this.end=0;this.busy=false;this.keep=null;this.kept=null;this.jumpOffset=0;
            this.handoff=new Map([...this.host.querySelectorAll(".novel-card")].map(c=>[`${c.dataset.source}:${c.dataset.novelId}`,c]));
            this.grid.replaceChildren();this.host.replaceChildren(this.top,this.grid,this.bottom,this.status);
            this.top.style.height=this.bottom.style.height='0px';this.status.hidden=true;this.host.dataset.feedReady="false";
            this.floatMenu(false);
            if(scrollY>this.host.offsetTop)window.scrollTo({top:Math.max(0,(this.controls.contains(document.activeElement)?this.controls.offsetTop:this.host.offsetTop)-12),behavior:'instant'});
        }
        accept(identity,result) {
            const fresh=identity!==this.identity;
            if(fresh)this.reset(identity);
            this.total=result.total;this.failed=null;
            const page=result.page||1;
            // Preview rows stay locked while the full catalog finishes importing.
            if(!this.cache.has(page))this.cache.set(page,result.rows);
            this.extent=Math.max(this.extent,Math.min(this.total,page*this.size));
            if(fresh&&page>1)this.jumpRow=Math.floor((page-1)*this.size/this.columns);
            this.dirty=true;this.rebuild();this.schedule();
        }
        rebuild() {
            const count=Math.ceil(this.extent/this.columns);this.prefix=[0];
            for(let i=0;i<count;i++)this.prefix.push(this.prefix[i]+(this.heights[i]||this.estimate));
        }
        rowAt(y) {
            let lo=0,hi=Math.max(0,this.prefix.length-2);
            while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(this.prefix[mid]<=y)lo=mid;else hi=mid-1;}
            return lo;
        }
        schedule() {
            if(!this.enabled||this.frame)return;
            this.frame=requestAnimationFrame(()=>{this.frame=0;this.update();});
        }
        async request(page,prefetch=false) {
            if(this.cache.has(page))return;
            const pending=this.pending.get(page);
            // A prefetch yields to other catalog queries; a slice that is needed now retries instead of failing.
            if(pending)return prefetch||!pending.prefetch?pending:pending.catch(()=>this.request(page));
            const epoch=this.epoch,identity=this.identity;
            const task=(async()=>{
                const result=await this.load(page,identity,prefetch);
                if(epoch!==this.epoch||!this.enabled)return;
                if(result.page!==page||!result.rows.length)throw new Error('Catalog is still loading');
                this.cache.set(page,result.rows);this.extent=Math.max(this.extent,Math.min(this.total,(page-1)*this.size+result.rows.length));
                this.rebuild();
            })();
            task.prefetch=prefetch;this.pending.set(page,task);
            try{await task;}finally{if(epoch===this.epoch)this.pending.delete(page);}
        }
        async update() {
            if(!this.enabled||!this.identity||this.busy||document.querySelector('dialog[open]'))return;
            const {columns,overscan}=this.metrics();
            if(columns!==this.columns||this.keep){
                // Only a reader scrolled into the feed is taken back to their card; one at the top stays there.
                const keep=this.keep;this.keep=null;
                if(columns!==this.columns){this.columns=columns;this.heights=[];this.dirty=true;this.rebuild();}
                if(keep){this.jumpRow=Math.floor(keep.index/columns);this.jumpOffset=keep.offset;}
            }
            if(!this.total){for(const card of [...this.grid.children,...this.handoff.values()]){card.remove();window.NovelCovers.release?.(card);}this.handoff.clear();this.grid.replaceChildren();this.mounted();this.status.hidden=false;this.status.disabled=true;this.status.textContent='No novels match these filters.';this.host.dataset.feedReady='true';return;}
            const epoch=this.epoch;
            const local=Math.max(0,-this.host.getBoundingClientRect().top);
            if(this.jumpRow==null)this.prefetch(local);
            const row=this.jumpRow ?? this.rowAt(local),rows=Math.min(8,Math.max(2*overscan,Math.ceil(innerHeight/this.estimate)+2*overscan));
            const start=this.jumpRow ?? Math.max(0,row-overscan),last=Math.min(Math.ceil(this.total/columns),start+rows);
            if(start===this.start&&last===this.end&&this.grid.children.length&&!this.dirty)return;
            const firstPage=Math.floor(start*columns/this.size)+1,lastPage=Math.floor(Math.max(0,last*columns-1)/this.size)+1;
            this.busy=true;let waited=false;
            try {
                // One request at a time respects the catalog worker's query cancellation.
                for(let page=firstPage;page<=lastPage;page++){
                    if(this.failed===page){this.status.hidden=false;this.status.disabled=false;this.status.textContent=this.failureText;return;}
                    if(!this.cache.has(page)){waited=true;this.status.hidden=false;this.status.disabled=true;this.status.textContent='Loading more novels…';}
                    try{await this.request(page);}catch(error){if(epoch!==this.epoch)return;this.failed=page;this.status.hidden=false;this.status.disabled=false;this.failureText=`${error.message}. Tap to retry.`;this.status.textContent=this.failureText;return;}
                    if(epoch!==this.epoch)return;
                }
                const previous=new Map([...this.handoff,...[...this.grid.children].map(c=>[`${c.dataset.source}:${c.dataset.novelId}`,c])]);
                const wanted=[];
                for(let index=start*columns;index<Math.min(this.total,last*columns);index++){
                    const record=this.cache.get(Math.floor(index/this.size)+1)?.[index%this.size];
                    if(!record)break;
                    const key=`${record.source}:${record.id}`;
                    const element=this.card(record,previous.get(key));element.dataset.feedIndex=index;wanted.push(element);
                }
                this.start=start;this.end=last;
                // Remove departing cards before inserting new ones: moving a kept card re-attaches it and
                // forces style and layout for every card in the window instead of only the new row.
                const keep=new Set(wanted);for(const child of [...this.grid.children])if(!keep.has(child)){child.remove();window.NovelCovers.release?.(child);}
                for(let i=0;i<wanted.length;i++)if(this.grid.children[i]!==wanted[i])this.grid.insertBefore(wanted[i],this.grid.children[i]||null);
                for(const old of this.handoff.values())if(!keep.has(old)&&!old.isConnected)window.NovelCovers.release?.(old);
                this.unobserve(keep);for(const card of wanted)if(!this.observed.has(card)){this.resize.observe(card);this.observed.add(card);}
                for(const page of this.cache.keys())if(page<firstPage-1||page>lastPage+2)this.cache.delete(page);
                this.top.style.height=(this.prefix[start]||0)+'px';
                this.bottom.style.height=Math.max(0,(this.prefix[this.prefix.length-1]||0)-(this.prefix[last]||0))+'px';
                this.status.hidden=!this.failed&&this.extent<this.total;this.status.disabled=!this.failed;
                this.status.textContent=this.failed?this.failureText:this.extent>=this.total?'End of results':'';
                this.host.dataset.feedStart=start*columns;this.host.dataset.feedEnd=Math.min(this.total,last*columns);this.host.dataset.feedCached=this.cache.size;
                this.handoff.clear();this.dirty=false;this.host.dataset.feedReady="true";
                // Without a wait the scroll offset read before the DOM writes is still current.
                this.mounted();this.measure(waited?undefined:local);
                if(this.jumpRow!=null){window.scrollTo({top:scrollY+this.host.getBoundingClientRect().top+(this.prefix[this.jumpRow]||0)-this.jumpOffset,behavior:"instant"});this.jumpRow=null;this.jumpOffset=0;this.settle();}
            } finally {if(epoch===this.epoch){this.busy=false;if(!this.failed)this.schedule();}}
        }
        // Each slice is a full catalog query (about half a second), so ask for the next one while the
        // reader is still a few screens away; waiting until the end is in view stalls fast scrolling.
        prefetch(local) {
            if(this.extent>=this.total||this.failed||this.pending.size||(this.prefix.at(-1)||0)-local>4*innerHeight)return;
            const epoch=this.epoch;
            this.request(Math.floor(this.extent/this.size)+1,true).then(()=>{if(epoch===this.epoch){this.dirty=true;this.schedule();}},()=>{});
        }
        measure(local) {
            if(!this.enabled||!this.grid.children.length)return;
            const {columns,gap}=this.metrics();
            if(this.columns!==columns){this.schedule();return;}
            // Heights come from the ResizeObserver; cards it has not reported yet keep the row's estimate.
            const children=this.grid.children;
            local??=Math.max(0,-this.host.getBoundingClientRect().top);
            const anchor=this.rowAt(local),old=this.prefix[anchor]||0;
            let changed=false;
            for(let i=0;i<children.length;i+=columns){
                let height=0;
                for(let j=i;j<Math.min(i+columns,children.length);j++)height=Math.max(height,children[j]._feedHeight||0);
                if(!height)continue;
                height+=gap;
                const row=this.start+i/columns;
                if(Math.abs((this.heights[row]||this.estimate)-height)>1){this.heights[row]=height;changed=true;}
            }
            if(!changed)return;
            if(this.start===0)this.estimate=this.heights[0]||this.estimate;
            this.rebuild();this.top.style.height=(this.prefix[this.start]||0)+'px';
            this.bottom.style.height=Math.max(0,this.prefix.at(-1)-(this.prefix[this.end]||this.prefix.at(-1)))+'px';
            const delta=(this.prefix[anchor]||0)-old;
            if(anchor>0&&Math.abs(delta)>1)window.scrollBy({top:delta,behavior:'instant'});
        }
        menu() {
            const y=Math.max(0,scrollY),delta=y-this.lastY;this.lastY=y;
            if(!this.enabled||document.querySelector('dialog[open]')){this.direction=0;this.lastScrollAt=0;return;}
            // Scrolls from a zoom or from the feed putting the reader back on their card are not an upward gesture.
            if(performance.now()<this.settleUntil||this.keep||this.jumpRow!=null){this.direction=0;return;}
            if(Math.abs(delta)<2)return;
            this.kept=null;
            const now=performance.now();
            // Small corrections separated by a pause are not one upward gesture.
            if(now-this.lastScrollAt>400||(delta<0)!==(this.direction<0))this.direction=0;
            this.lastScrollAt=now;
            this.direction+=delta;
            // A docked menu changes only on a deliberate upward gesture, so other scrolls skip the layout read.
            if(this.placeholder.hidden&&this.direction>=-240)return;
            const edge=this.placeholder.hidden?this.controls.getBoundingClientRect().bottom+y:this.placeholder.offsetTop+this.placeholder.offsetHeight;
            if(y<=edge){this.floatMenu(false);return;}
            if(this.direction<-240)this.floatMenu(true);
            else if(this.direction>12&&!(this.controls.contains(document.activeElement)&&document.activeElement.matches('input[type=text],input[type=search],select,textarea')))this.floatMenu(false);
        }
        floatMenu(show) {
            if(show===this.controls.classList.contains('mobile-controls-floating'))return;
            if(show){this.placeholder.style.height=this.controls.getBoundingClientRect().height+'px';this.placeholder.style.marginBottom=getComputedStyle(this.controls).marginBottom;this.placeholder.hidden=false;}
            this.controls.classList.toggle('mobile-controls-floating',show);
            if(!show)this.placeholder.hidden=true;
        }
    };
})();
