/* Compact controls retain native select values and keyboard access. */
(function () {
    'use strict';
    const mobile = matchMedia('(max-width: 650px)');
    const icons = {all:'halgakos.ico',npia:'icons/npia.png',kpage:'icons/kpage.png',sfc:'icons/sfc.ico',
        nweb:'icons/nweb.png',jara:'icons/jara.ico',mpia:'icons/mpia.ico',rbooks:'icons/rbooks.png',nseries:'icons/nseries.png'};
    const shortSources = {all:'All',npia:'NP',kpage:'KP',sfc:'SF',nweb:'NW',jara:'JR',mpia:'MP',rbooks:'RB',nseries:'NS'};
    const sorts = {daily:'DAY',weekly:'WK',monthly:'MO',updated:'UPD',likes:'LIKES',views:'VIEWS',chapters:'CH',title:'A–Z',
        'metric:favorites':'FAV','metric:recommendations':'REC','metric:rating':'RATE','metric:downloads':'DL',
        sfc_popularity:'POPULAR',sfc_bestseller:'BEST',sfc_newbooks:'NEW',sfc_bookmarks:'SAVED',sfc_jp:'JP'};
    const pickers = [];
    let activePicker;
    function createPicker(kind, label) {
        const select = document.getElementById(kind + 'Select'), host = select.closest('.control-group');
        const source = kind === 'source';
        const button = document.createElement('button'), menu = document.createElement('div');
        button.type='button';button.id=kind+'PickerButton';button.className='source-picker-button'+(source?'':' mobile-picker-button');
        button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls',kind+'PickerMenu');
        menu.id=kind+'PickerMenu';menu.className='source-picker-menu';menu.hidden=true;menu.setAttribute('role','listbox');menu.setAttribute('aria-label',source?'Sources':label);
        const symbol = document.createElement(source?'img':'span'), text = document.createElement('span');
        symbol.className='picker-symbol';symbol.setAttribute('aria-hidden','true');
        if(source){symbol.alt='';symbol.width=symbol.height=20;}
        text.className='picker-value';button.append(symbol,text);host.append(button,menu);
        const items = new Map();
        function sync() {
            const chosen = select.selectedOptions[0] || select.options[0];
            if (!chosen) return;
            const value=chosen.value;
            if(source){
                const src=icons[value]||icons.all;
                if(symbol.getAttribute('src')!==src)symbol.src=src;
                text.textContent=mobile.matches?(shortSources[value]||chosen.textContent):chosen.textContent;
            } else {
                symbol.className='picker-symbol';symbol.hidden=false;
                if(kind==='audience'){
                    symbol.className='picker-symbol audience-badge '+value;
                    symbol.textContent={general:'G',r15:'15+',adult:'19+'}[value]||'';
                    symbol.hidden=value==='all';text.textContent=value==='all'?'All':'';
                } else if(kind==='status'){
                    symbol.textContent='●';symbol.classList.add('status-symbol');text.textContent={all:'ALL',ongoing:'ON',complete:'DONE'}[value];
                } else if(kind==='order'){symbol.textContent=value==='asc'?'↑':'↓';text.textContent=value==='asc'?'ASC':'DESC';}
                else if(kind==='batch'){symbol.textContent='#';text.textContent=value;}
                else {symbol.textContent=['daily','weekly','monthly'].includes(value)||value.startsWith('rank:')?'🏆':'◷';text.textContent=sorts[value]||chosen.textContent.replace(/ \(.*$/, '');}
            }
            button.title=select.title||`${label}: ${chosen.textContent}`;
            button.setAttribute('aria-label',`${label}: ${chosen.textContent}`);
            for(const option of select.options){
                let item=items.get(option.value);
                if(!item){
                    item=document.createElement('button');item.type='button';item.className='source-picker-option';item.tabIndex=-1;item.setAttribute('role','option');
                    if(source){const img=document.createElement('img');img.src=icons[option.value]||icons.all;img.alt='';img.width=img.height=20;item.append(img);item.dataset.source=option.value;}
                    if(kind==='audience'&&option.value!=='all'){const badge=document.createElement('span');badge.className='audience-badge '+option.value;badge.setAttribute('aria-hidden','true');badge.textContent={general:'G',r15:'15+',adult:'19+'}[option.value];item.append(badge);}
                    const name=document.createElement('span');item.append(name);items.set(option.value,item);menu.append(item);
                    item.addEventListener('click',()=>{if(option.disabled)return;select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));close(true);});
                }
                item.lastElementChild.textContent=option.textContent;item.disabled=option.disabled;item.hidden=option.hidden;item.title=option.title;
                item.setAttribute('aria-selected',String(option.value===value));
            }
            const values=new Set(Array.from(select.options,option=>option.value));
            for(const [value,item] of items)if(!values.has(value)){item.remove();items.delete(value);}
            const custom=source||mobile.matches;
            select.classList.toggle('source-select-native',custom);
            select.tabIndex=custom?-1:0;
            if(custom)select.setAttribute('aria-hidden','true');else select.removeAttribute('aria-hidden');
            host.querySelector('label').htmlFor=custom?button.id:select.id;
            if(!custom)close();
        }
        function close(focus=false){menu.hidden=true;button.setAttribute('aria-expanded','false');if(activePicker===close)activePicker=null;if(focus)button.focus();}
        function open(){
            activePicker?.();sync();
            const rect=button.getBoundingClientRect(),below=innerHeight-rect.bottom;
            const above=below<160&&rect.top>below;
            menu.style.top=above?'auto':'';menu.style.bottom=above?'calc(100% + 6px)':'';
            menu.style.maxHeight=Math.max(80,Math.min(420,(above?rect.top:below)-12))+'px';
            menu.hidden=false;button.setAttribute('aria-expanded','true');activePicker=close;
            (items.get(select.value)||[...items.values()].find(item=>!item.disabled&&!item.hidden))?.focus();
        }
        button.addEventListener('click',()=>menu.hidden?open():close());
        button.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();open();}});
        menu.addEventListener('keydown',event=>{
            if(event.key==='Escape'){event.preventDefault();close(true);return;}
            if(event.key==='Tab'){close();return;}
            const enabled=[...items.values()].filter(item=>!item.disabled&&!item.hidden),at=enabled.indexOf(document.activeElement);
            let next;
            if(event.key==='ArrowDown')next=(at+1)%enabled.length;
            if(event.key==='ArrowUp')next=(at-1+enabled.length)%enabled.length;
            if(event.key==='Home')next=0;if(event.key==='End')next=enabled.length-1;
            if(next!=null){event.preventDefault();enabled[next]?.focus();}
        });
        document.addEventListener('pointerdown',event=>{if(!host.contains(event.target))close();});
        select.addEventListener('change',sync);
        new MutationObserver(sync).observe(select,{subtree:true,attributes:true,childList:true,characterData:true,attributeFilter:['disabled','hidden','label','title','selected']});
        pickers.push(sync);sync();return sync;
    }
    const syncSource=createPicker('source','Source');
    for(const [kind,label] of [['status','Status'],['audience','Audience'],['order','Order'],['batch','Per page'],['sort','Sort by']])createPicker(kind,label);
    const sync=()=>pickers.forEach(update=>update());
    mobile.addEventListener('change',sync);
    window.NovelSourcePicker={sync:syncSource,iconFor:source=>icons[source]||icons.all};
    window.NovelControlPicker={sync};
})();
