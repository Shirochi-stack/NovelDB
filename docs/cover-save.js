/* Original image downloads and a full metadata dialog, independent of card compression. */
(function () {
    'use strict';
    let current=null, contextCard=null, noticeTimer, viewController=null, viewSequence=0, returnFocus=null;
    const menu=document.createElement('div');
    menu.className='cover-save-menu';menu.hidden=true;
    menu.setAttribute('role','menu');menu.setAttribute('aria-label','Cover image');
    const save=document.createElement('button'), details=document.createElement('button');
    for(const button of [save,details]) {button.type='button';button.setAttribute('role','menuitem');}
    save.textContent='Download Image';details.textContent='View Details';
    menu.append(save,details);document.body.append(menu);
    const notice=document.createElement('div');
    notice.className='cover-save-notice';notice.hidden=true;notice.setAttribute('role','status');document.body.append(notice);
    function message(text,card) {
        clearTimeout(noticeTimer);notice.replaceChildren(document.createTextNode(text));notice.hidden=false;
        if(card) {
            const button=document.createElement('button');button.type='button';button.textContent='View Details';
            button.addEventListener('click',()=>{notice.hidden=true;openDetails(card);});notice.append(' ',button);
        }
        noticeTimer=setTimeout(()=>{notice.hidden=true;notice.replaceChildren();},card ? 15000 : 6000);
    }
    function originalUrl(card) {
        const cover=card?.querySelector('.card-cover[data-src]');
        const value=cover?.dataset.originalSrc || cover?.dataset.src || card?._record?.cover;
        if(!value) throw new Error('Original image is unavailable.');
        const url=new URL(value,location.href);
        if(!['https:','http:'].includes(url.protocol) || url.username || url.password) throw new Error('Original image is unavailable.');
        return url.href;
    }
    async function readOriginal(url,signal) {
        let failure;
        for(const referrerPolicy of ['no-referrer','strict-origin-when-cross-origin']) {
            const controller=new AbortController(), abort=()=>controller.abort(signal.reason);
            signal.addEventListener('abort',abort,{once:true});if(signal.aborted) abort();
            const timer=setTimeout(()=>controller.abort(new Error('Image download timed out')),15000);
            try {
                const response=await fetch(url,{mode:'cors',credentials:'omit',referrerPolicy,signal:controller.signal});
                if(!response.ok) {await response.body?.cancel();throw new Error(`Image HTTP ${response.status}`);}
                return await response.blob();
            } catch(error) {if(signal.aborted) throw error;failure=error;}
            finally {clearTimeout(timer);signal.removeEventListener('abort',abort);}
        }
        throw failure;
    }
    async function imageType(blob) {
        // URLs and Content-Disposition can use private file extensions; inspect
        // the original bytes rather than assuming the URL's suffix is a format.
        const bytes=new Uint8Array(await blob.slice(0,4096).arrayBuffer());
        const text=new TextDecoder().decode(bytes), tag=(at,length)=>String.fromCharCode(...bytes.subarray(at,at+length));
        if(tag(0,8)==='\x89PNG\r\n\x1a\n') return ['png','image/png'];
        if(bytes[0]===255 && bytes[1]===216 && bytes[2]===255) return ['jpg','image/jpeg'];
        if(['GIF87a','GIF89a'].includes(tag(0,6))) return ['gif','image/gif'];
        if(tag(0,4)==='RIFF' && tag(8,4)==='WEBP') return ['webp','image/webp'];
        if(tag(4,4)==='ftyp') {
            const brands=tag(8,56);
            if(/avif|avis/.test(brands)) return ['avif','image/avif'];
            if(/heic|heix|hevc|hevx/.test(brands)) return ['heic','image/heic'];
        }
        if(tag(0,2)==='BM') return ['bmp','image/bmp'];
        if(bytes[0]===0 && bytes[1]===0 && bytes[2]===1 && bytes[3]===0) return ['ico','image/x-icon'];
        if(/^\s*(?:<\?xml[\s\S]*?\?>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg(?:\s|>)/i.test(text)) return ['svg','image/svg+xml'];
        throw new Error('The server did not return a supported image.');
    }
    async function downloadImage(url,signal) {
        try {
            const blob=await readOriginal(url,signal);
            return {blob,format:await imageType(blob),helper:false};
        } catch(error) {
            if(signal.aborted) throw error;
            // Static hosting cannot read a CDN response without CORS. Ask the
            // public image helper for a lossless WebP at its original size,
            // including every animation frame. Never submit a local address.
            const source=new URL(url), host=source.hostname;
            if(host==='localhost' || host.endsWith('.localhost') || !host.includes('.')
                    || /^\d+\.\d+\.\d+\.\d+$/.test(host) || host.includes(':')) throw error;
            const helper=new URL('https://\u0077\u0073\u0072\u0076.\u006e\u006c/');
            helper.search=new URLSearchParams({url,output:'webp',ll:'1',n:'-1'});
            message('Preparing full-resolution image through an image helper…');
            const blob=await readOriginal(helper.href,signal), format=await imageType(blob);
            if(format[0]!=='webp' || !await losslessWebp(blob)) throw new Error('The image helper did not return a lossless WebP image.');
            return {blob,format,helper:true};
        }
    }
    async function losslessWebp(blob) {
        const bytes=new Uint8Array(await blob.arrayBuffer()), data=new DataView(bytes.buffer);
        const tag=at=>String.fromCharCode(...bytes.subarray(at,at+4));
        function scan(start,end) {
            let frames=0;
            for(let at=start;at+8<=end;) {
                const kind=tag(at),size=data.getUint32(at+4,true),next=at+8+size;
                if(next>end || kind==='VP8 ') return -1;
                if(kind==='VP8L') frames++;
                else if(kind==='ANMF') {
                    const nested=scan(at+24,next);
                    if(nested<1) return -1;
                    frames+=nested;
                }
                at=next+(size%2);
            }
            return frames;
        }
        return scan(12,bytes.length)>0;
    }
    async function download(card) {
        if(!card) return;
        if(current) {message('An image download is being prepared.');return;}
        let url;
        try {url=originalUrl(card);} catch(error) {message(error.message);return;}
        const controller=current=new AbortController();
        const button=card.querySelector('.cover-save');
        if(button) {button.disabled=true;button.setAttribute('aria-busy','true');}
        message('Preparing image download…');
        try {
            const result=await downloadImage(url,controller.signal), [extension,type]=result.format;
            const blob=new Blob([result.blob],{type}), href=URL.createObjectURL(blob), link=document.createElement('a');
            const name=`${card.dataset.source || 'cover'}-${card.dataset.novelId || 'image'}`.replace(/[^a-zA-Z0-9_-]/g,'_');
            link.href=href;link.download=`${name}.${extension}`;link.hidden=true;
            document.body.append(link);link.click();link.remove();
            setTimeout(()=>URL.revokeObjectURL(href),30000);
            message(result.helper ? 'Full-resolution WebP download started using the image helper.' : 'Image download started.');
        } catch(error) {
            message(error instanceof TypeError ? 'This image host blocks direct browser downloads. You can still view the original in Details.' : error.message,card);
        } finally {
            if(button) {button.disabled=false;button.removeAttribute('aria-busy');}current=null;
        }
    }
    const dialog=document.createElement('dialog');dialog.className='novel-details';dialog.setAttribute('aria-labelledby','novelDetailsTitle');
    const header=document.createElement('div');header.className='novel-details-header';
    const title=document.createElement('h2');title.id='novelDetailsTitle';
    const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label','Close Details');
    header.append(title,close);
    const body=document.createElement('div');body.className='novel-details-body';
    const figure=document.createElement('figure');figure.className='novel-details-image';
    const image=document.createElement('img');image.alt='Original cover';image.referrerPolicy='no-referrer';
    const imageStatus=document.createElement('figcaption');imageStatus.setAttribute('role','status');
    const detailDownload=document.createElement('button');detailDownload.type='button';detailDownload.className='page-btn';detailDownload.textContent='Download Image';
    figure.append(image,imageStatus,detailDownload);
    const fields=document.createElement('dl');fields.className='novel-details-fields';body.append(figure,fields);
    dialog.append(header,body);document.body.append(dialog);
    let detailCard=null;
    const labels={title:'Original title',canonicalUrl:'Novel URL',purchaseUrl:'Purchase URL',
        synopsis:'Synopsis',id:'ID',complete:'Complete',age:'Age',metrics:'Other details',rankings:'Rankings'};
    const fieldLabel=key=>labels[key] || key.replace(/([a-z])([A-Z])/g,'$1 $2').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
    function renderMetadata(record) {
        fields.replaceChildren();
        const adult=window.NovelMetadata.matchesAudience(record,'adult');
        const teen=window.NovelMetadata.matchesAudience(record,'r15');
        const rankEntries=[];
        const isRank=key=>/Rank(?:Adult|Teen)?$/.test(key);
        const relevantRank=key=>(!/adult|r19/i.test(key) || adult) && (!/teen|r15/i.test(key) || teen);
        for(const [key,value] of Object.entries(record)) {
            if(isRank(key) && value>0 && relevantRank(key)) rankEntries.push([key,value]);
        }
        for(const [key,value] of Object.entries(record.rankings || {})) {
            if(value>0 && relevantRank(key)) rankEntries.push([key,value]);
        }
        function add(key,value,wide=false) {
            const group=document.createElement('div'),term=document.createElement('dt'),definition=document.createElement('dd');
            group.className='novel-details-field'+(wide ? ' details-wide' : '');
            term.textContent=fieldLabel(key);definition.dataset.field=key;
            if(key==='canonicalUrl' || key==='purchaseUrl') {
                if(!/^https?:\/\//i.test(String(value))) return;
                const link=document.createElement('a');link.href=value;link.textContent=key==='canonicalUrl' ? 'Open novel ↗' : 'Purchase ↗';
                link.target='_blank';link.rel='noopener';definition.append(link);
            } else if(key==='age') definition.textContent=value==null ? 'Not available' : adult ? '19+' : teen ? '15+' : Number(value)===0 ? 'General' : Number(value)>0 ? `${value}+` : 'Not available';
            else if(key==='complete') definition.textContent=value==null ? 'Not available' : [true,1,'1'].includes(value) ? 'Yes' : 'No';
            else if(key==='rankings') {
                for(const [name,rank] of rankEntries) {
                    const chip=document.createElement('span');chip.className='details-rank';
                    chip.textContent=`${fieldLabel(name).replace(/ Rank(?= Adult| Teen|$)/,'')}: #${rank}`;
                    chip.dataset.field=name;definition.append(chip);
                }
            } else if(value && typeof value==='object' && !Array.isArray(value)) {
                const pairs=document.createElement('dl');pairs.className='details-properties';
                for(const [name,item] of Object.entries(value)) {
                    if(item==null || item==='' || (typeof item==='object' && !Object.keys(item).length)) continue;
                    const label=document.createElement('dt'),text=document.createElement('dd');
                    label.textContent=fieldLabel(name);text.textContent=typeof item==='object' ? JSON.stringify(item)
                        : typeof item==='boolean' ? (item ? 'Yes' : 'No') : String(item);
                    pairs.append(label,text);
                }
                if(!pairs.children.length) return;
                definition.append(pairs);
            } else definition.textContent=value==null || value==='' ? 'Not available' : typeof value==='boolean' ? (value ? 'Yes' : 'No')
                : Array.isArray(value) ? value.join(', ') : typeof value==='number' ? value.toLocaleString('en') : String(value);
            group.append(term,definition);fields.append(group);
        }
        const priority=['title','canonicalUrl','author','source','id','complete','age','updated','views','likes','chapters','tier','synopsis','tags'];
        for(const key of new Set([...priority,...Object.keys(record)])) {
            const value=record[key];
            if(!(key in record) || key.startsWith('_') || ['cover','metadataV1','titleEn','rankings'].includes(key) || isRank(key)) continue;
            if(key==='title' && !record.titleEn) continue;
            if((value==null || value==='' || (typeof value==='object' && !Object.keys(value).length)) && !['complete','age'].includes(key)) continue;
            add(key,value,['title','canonicalUrl','synopsis','tags','metrics'].includes(key) || (value!==null && typeof value==='object'));
        }
        if(rankEntries.length) add('rankings',rankEntries,true);
    }
    async function openDetails(card) {
        if(!card) return;
        closeMenu();viewController?.abort();viewController=new AbortController();
        const signal=viewController.signal, sequence=++viewSequence;
        detailCard=card;returnFocus=card.querySelector('.cover-details') || card.querySelector('a');
        title.textContent=card._record?.titleEn || card._record?.title || 'Novel details';
        title.dataset.field=card._record?.titleEn ? 'titleEn' : 'title';
        renderMetadata({...card._record,canonicalUrl:card._record?.canonicalUrl || card.querySelector('.card-cover-wrap')?.href,synopsis:card._synopsis || card._record?.synopsis || 'Loading synopsis…'});
        image.removeAttribute('src');image.hidden=false;detailDownload.disabled=false;
        imageStatus.textContent='Loading original image…';
        let attempt=0, imageTimer;
        const finishImage=text=>{clearTimeout(imageTimer);imageStatus.textContent=text;};
        image.onload=()=>finishImage(`Original image · ${image.naturalWidth} × ${image.naturalHeight}`);
        image.onerror=()=>{
            if(signal.aborted) return;
            if(!attempt++) {image.referrerPolicy='strict-origin-when-cross-origin';image.src=originalUrl(card);}
            else {image.hidden=true;finishImage('The original image could not load.');}
        };
        signal.addEventListener('abort',()=>{clearTimeout(imageTimer);image.onload=image.onerror=null;image.removeAttribute('src');},{once:true});
        try {
            image.referrerPolicy='no-referrer';image.src=originalUrl(card);
            imageTimer=setTimeout(()=>{image.removeAttribute('src');image.hidden=true;finishImage('The original image did not load in time. Reopen Details to retry.');},20000);
        } catch(error) {image.hidden=true;detailDownload.disabled=true;finishImage(error.message);}
        if(!dialog.open) dialog.showModal();
        close.focus({preventScroll:true});
        try {
            const record=await window.NovelDetailsData?.(card,signal);
            if(record && !signal.aborted && sequence===viewSequence) renderMetadata(record);
        } catch(error) {
            if(!signal.aborted && sequence===viewSequence) {const synopsis=fields.querySelector('[data-field="synopsis"]');if(synopsis)synopsis.textContent='Synopsis could not load. Reopen Details to retry.';}
        }
    }
    close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{viewSequence++;viewController?.abort();viewController=null;detailCard=null;fields.replaceChildren();returnFocus?.focus({preventScroll:true});returnFocus=null;});
    dialog.addEventListener('click',event=>{
        const rect=dialog.getBoundingClientRect();
        if(event.target===dialog && (event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom)) dialog.close();
    });
    detailDownload.addEventListener('click',()=>download(detailCard));
    function closeMenu() {menu.hidden=true;contextCard=null;}
    document.addEventListener('contextmenu',event=>{
        const wrap=event.target.closest?.('.card-cover-wrap'), card=wrap?.closest('.novel-card');
        if(!card) return;
        event.preventDefault();contextCard=card;
        try {originalUrl(card);save.disabled=false;} catch(_) {save.disabled=true;}
        menu.hidden=false;menu.style.left='0px';menu.style.top='0px';
        const rect=wrap.getBoundingClientRect(), x=event.clientX || rect.left+rect.width/2, y=event.clientY || rect.top+rect.height/2;
        menu.style.left=`${Math.max(8,Math.min(x,innerWidth-menu.offsetWidth-8))}px`;
        menu.style.top=`${Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))}px`;
        (save.disabled ? details : save).focus({preventScroll:true});
    });
    document.addEventListener('click',event=>{
        const button=event.target.closest?.('.cover-save,.cover-details');
        if(button) {event.preventDefault();const card=button.closest('.novel-card');button.classList.contains('cover-save') ? download(card) : openDetails(card);}
    });
    save.addEventListener('click',()=>{const card=contextCard;closeMenu();download(card);});
    details.addEventListener('click',()=>{const card=contextCard;closeMenu();openDetails(card);});
    document.addEventListener('pointerdown',event=>{if(!menu.contains(event.target)) closeMenu();});
    document.addEventListener('keydown',event=>{
        if(menu.hidden) return;
        if(event.key==='Escape' || event.key==='Tab') {const card=contextCard;closeMenu();card?.querySelector('a')?.focus();return;}
        if(['ArrowUp','ArrowDown'].includes(event.key)) {event.preventDefault();(document.activeElement===save ? details : save).focus();}
    });
    window.addEventListener('resize',closeMenu);document.addEventListener('wheel',closeMenu,{passive:true});
})();
