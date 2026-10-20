/* Original-resolution WebP downloads are separate from retained thumbnails. */
(function () {
    'use strict';
    let current = null, contextCover = null, noticeTimer;
    const menu = document.createElement('div');
    menu.className = 'cover-save-menu'; menu.hidden = true;
    menu.setAttribute('role','menu'); menu.setAttribute('aria-label','Cover image');
    const save = document.createElement('button'), original = document.createElement('a');
    save.type = 'button'; save.textContent = 'Save original WebP'; save.setAttribute('role','menuitem');
    original.textContent = 'Open original image'; original.setAttribute('role','menuitem');
    original.target = '_blank'; original.rel = 'noopener'; original.referrerPolicy = 'no-referrer';
    menu.append(save,original); document.body.append(menu);
    const notice = document.createElement('div');
    notice.className = 'cover-save-notice'; notice.hidden = true; notice.setAttribute('role','status');
    document.body.append(notice);
    function message(text, url = '') {
        clearTimeout(noticeTimer); notice.replaceChildren(document.createTextNode(text)); notice.hidden = false;
        if (url) {
            const link = document.createElement('a');
            link.textContent = 'Open original image'; link.href = url;
            link.target = '_blank'; link.rel = 'noopener'; link.referrerPolicy = 'no-referrer';
            notice.append(' ',link);
        }
        noticeTimer = setTimeout(()=>{notice.hidden=true;notice.replaceChildren();},url ? 20000 : 6000);
    }
    function originalUrl(cover) {
        const url = new URL(cover.dataset.originalSrc || cover.dataset.src,location.href);
        if (!['https:','http:'].includes(url.protocol) || url.username || url.password) throw new Error('Original image is unavailable.');
        return url.href;
    }
    async function readOriginal(url,signal) {
        let failure;
        for (const referrerPolicy of ['no-referrer','strict-origin-when-cross-origin']) {
            try {
                const response = await fetch(url,{mode:'cors',credentials:'omit',referrerPolicy,signal});
                if (!response.ok) {await response.body?.cancel();throw new Error(`Image HTTP ${response.status}`);}
                return await response.blob();
            } catch (error) {if (signal.aborted) throw error;failure=error;}
        }
        throw failure;
    }
    async function webpKind(blob) {
        const bytes = new Uint8Array(await blob.arrayBuffer()), data = new DataView(bytes.buffer);
        const tag = at => String.fromCharCode(...bytes.subarray(at,at+4));
        if (bytes.length<12 || tag(0)!=='RIFF' || tag(8)!=='WEBP') return '';
        for (let at=12;at+8<=bytes.length;) {
            const kind=tag(at),size=data.getUint32(at+4,true);
            if (at+8+size>bytes.length) break;
            if (kind==='VP8L') return 'lossless';
            if (kind==='VP8 ') return 'lossy';
            if (kind==='ANIM') return 'animated';
            at+=8+size+(size%2);
        }
        return 'webp';
    }
    async function encodeOriginal(blob,signal) {
        // Keep existing WebP bytes, animation and metadata unchanged.
        if (await webpKind(blob)) return new Blob([blob],{type:'image/webp'});
        let bitmap, image, url, canvas;
        try {
            if (typeof createImageBitmap==='function') {
                try {bitmap=await createImageBitmap(blob);} catch (_) { /* Some browsers only decode SVG through an image element. */ }
            }
            if (!bitmap) {
                image=new Image(); url=URL.createObjectURL(blob); image.src=url;
                await image.decode();
            }
            if (signal.aborted) throw new DOMException('Save cancelled','AbortError');
            const pixels=bitmap || image;
            canvas=document.createElement('canvas');
            canvas.width=pixels.naturalWidth || pixels.width;
            canvas.height=pixels.naturalHeight || pixels.height;
            const context=canvas.getContext('2d');
            if (!context) throw new Error('Image conversion is unavailable.');
            context.drawImage(pixels,0,0);
            const encoded=await new Promise(resolve=>canvas.toBlob(resolve,'image/webp',1));
            if (!encoded || await webpKind(encoded)!=='lossless') throw new Error('This browser cannot export lossless WebP.');
            return encoded;
        } finally {
            bitmap?.close();
            if (image) image.removeAttribute('src');
            if (url) URL.revokeObjectURL(url);
            if (canvas) canvas.width=canvas.height=0;
        }
    }
    async function download(cover) {
        if (!cover) return;
        if (current) {message('A cover is being prepared. Please wait.');return;}
        let url;
        try {url=originalUrl(cover);} catch (error) {message(error.message);return;}
        const controller=current=new AbortController();
        const card=cover.closest('.novel-card'), button=card?.querySelector('.cover-save');
        if (button) {button.disabled=true;button.setAttribute('aria-busy','true');}
        message('Preparing original-resolution WebP…');
        try {
            const encoded=await encodeOriginal(await readOriginal(url,controller.signal),controller.signal);
            const href=URL.createObjectURL(encoded), link=document.createElement('a');
            const name=`${card?.dataset.source || 'cover'}-${card?.dataset.novelId || 'image'}`.replace(/[^a-zA-Z0-9_-]/g,'_');
            link.href=href;link.download=`${name}.webp`;link.hidden=true;
            document.body.append(link);link.click();link.remove();
            setTimeout(()=>URL.revokeObjectURL(href),60000);
            message('Original-resolution WebP download started.');
        } catch (error) {
            message(error instanceof TypeError ? 'This image host blocks browser conversion. You can open the original instead.'
                : `${error.message} You can open the original instead.`,url);
        } finally {
            if (button) {button.disabled=false;button.removeAttribute('aria-busy');}
            current=null;
        }
    }
    function closeMenu() {menu.hidden=true;contextCover=null;}
    document.addEventListener('contextmenu',event=>{
        const wrap=event.target.closest?.('.card-cover-wrap'), cover=wrap?.querySelector('.card-cover[data-src]');
        if (!cover) return;
        event.preventDefault();contextCover=cover;
        try {original.href=originalUrl(cover);} catch (_) {return;}
        menu.hidden=false;menu.style.left='0px';menu.style.top='0px';
        const rect=wrap.getBoundingClientRect();
        const x=event.clientX || rect.left+rect.width/2, y=event.clientY || rect.top+rect.height/2;
        menu.style.left=`${Math.max(8,Math.min(x,innerWidth-menu.offsetWidth-8))}px`;
        menu.style.top=`${Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))}px`;
        save.focus({preventScroll:true});
    });
    document.addEventListener('click',event=>{
        const button=event.target.closest?.('.cover-save');
        if (button) {event.preventDefault();download(button.closest('.novel-card')?.querySelector('.card-cover[data-src]'));}
    });
    save.addEventListener('click',()=>{const cover=contextCover;closeMenu();download(cover);});
    original.addEventListener('click',closeMenu);
    document.addEventListener('pointerdown',event=>{if (!menu.contains(event.target)) closeMenu();});
    document.addEventListener('keydown',event=>{
        if (menu.hidden) return;
        if (event.key==='Escape' || event.key==='Tab') {const cover=contextCover;closeMenu();cover?.closest('a')?.focus();return;}
        if (['ArrowUp','ArrowDown'].includes(event.key)) {
            event.preventDefault();(document.activeElement===save ? original : save).focus();
        }
    });
    window.addEventListener('resize',closeMenu);
    document.addEventListener('wheel',closeMenu,{passive:true});
})();
