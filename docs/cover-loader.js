/* Bounded, near-viewport cover decoding with small retained pixel buffers. */
(function () {
    'use strict';
    const MAX_WIDTH = 320, MAX_HEIGHT = 480, CONCURRENCY = 2;
    const queue = [], active = new Map(), blockedOrigins = new Set();
    const objectUrls = new Map(), recoverable = new WeakMap();
    let lowResolution = true;
    try {lowResolution=localStorage.getItem('noveldb.lowResCovers') !== 'false';} catch (_) {}
    function widthFor(img) {
        return Math.max(1,Math.min(MAX_WIDTH,Math.ceil((img.getBoundingClientRect().width || 240) * Math.min(devicePixelRatio || 1,1.5))));
    }
    async function drawThumbnail(img, image) {
        if (!img.isConnected) return;
        const width = image.naturalWidth || image.width, height = image.naturalHeight || image.height;
        const scale = Math.min(1,widthFor(img)/width,MAX_HEIGHT/height);
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1,Math.round(width*scale)); canvas.height = Math.max(1,Math.round(height*scale));
        canvas.className = img.className;
        canvas.dataset.src = img.dataset.src;
        canvas.setAttribute('aria-hidden','true');
        const context = canvas.getContext('2d');
        if (!context) throw new Error('Cover canvas unavailable');
        context.imageSmoothingEnabled = true; context.imageSmoothingQuality = 'high';
        context.drawImage(image,0,0,canvas.width,canvas.height);
        // Encoded thumbnails survive page suspension and discarded drawing
        // buffers. The browser can decode the small image again on return.
        let blob;
        try { blob = await new Promise(resolve => canvas.toBlob(resolve,'image/webp',0.72)); }
        catch (_) { /* Display-only cross-origin images taint the canvas. */ }
        if (blob?.type !== 'image/webp') blob = null;
        if (!img.isConnected) return;
        if (blob) {
            const thumbnail = document.createElement('img'), url = URL.createObjectURL(blob);
            thumbnail.className = img.className; thumbnail.dataset.src = img.dataset.src;
            thumbnail.alt = img.alt || ''; thumbnail.decoding = 'async';
            thumbnail._coverOptions = img._coverOptions; thumbnail._coverFallback = img._coverFallback;
            thumbnail.src = url;
            objectUrls.set(thumbnail,url);
            img.replaceWith(thumbnail); img.removeAttribute('src');
            return thumbnail;
        }
        recoverable.set(canvas,{...img._coverOptions,src:img.dataset.src,alt:img.alt});
        canvas.addEventListener('contextlost',event=>event.preventDefault());
        canvas.addEventListener('contextrestored',()=>restoreCanvas(canvas));
        img.replaceWith(canvas); img.removeAttribute('src');
        return canvas;
    }
    function restoreCanvas(canvas) {
        const job = recoverable.get(canvas);
        if (!job || !canvas.isConnected) return;
        const img = document.createElement('img');
        img.className = canvas.className.replace(/\bloaded\b/g,'');
        img.dataset.src = job.src; img.alt = job.alt || '';
        canvas.replaceWith(img);
        window.NovelCovers.load(img,job);
    }
    function restoreCovers() {
        if (document.visibilityState === 'hidden') return;
        // Only the CORS-restricted fallback retains a canvas. Rebuild these
        // bounded thumbnails after mobile tab suspension or history restore.
        for (const canvas of document.querySelectorAll('canvas.card-cover')) restoreCanvas(canvas);
    }
    document.addEventListener('visibilitychange',restoreCovers);
    window.addEventListener('pageshow',restoreCovers);
    function nativeImage(img,url,policy,signal) {
        return new Promise((resolve,reject)=>{
            const clean = () => {img.onload = img.onerror = null; signal.removeEventListener('abort',abort);};
            const abort = () => {clean();img.removeAttribute('src');reject(new DOMException('Cover cancelled','AbortError'));};
            img.onload = () => {clean();resolve(img);};
            img.onerror = () => {clean();reject(new Error('Cover image unavailable'));};
            signal.addEventListener('abort',abort,{once:true});
            if (signal.aborted) {abort();return;}
            img.referrerPolicy = policy; img.src = url;
        });
    }
    async function dimensions(blob) {
        const buffer = await blob.slice(0,131072).arrayBuffer(), b = new Uint8Array(buffer), v = new DataView(buffer);
        if (b.length >= 24 && v.getUint32(0) === 0x89504e47) return [v.getUint32(16),v.getUint32(20)];
        if (b.length >= 10 && b[0] === 71 && b[1] === 73 && b[2] === 70) return [v.getUint16(6,true),v.getUint16(8,true)];
        if (b[0] === 255 && b[1] === 216) {
            let p = 2;
            while (p + 8 < b.length && b[p] === 255) {
                while (b[p] === 255) p++;
                const marker = b[p++], length = v.getUint16(p);
                if (marker >= 0xc0 && marker <= 0xcf && ![0xc4,0xc8,0xcc].includes(marker)) return [v.getUint16(p+5),v.getUint16(p+3)];
                if (length < 2) break;
                p += length;
            }
        }
        if (b.length >= 30 && v.getUint32(0) === 0x52494646 && v.getUint32(8) === 0x57454250) {
            const kind = v.getUint32(12);
            if (kind === 0x56503858) return [1+b[24]+(b[25]<<8)+(b[26]<<16),1+b[27]+(b[28]<<8)+(b[29]<<16)];
            if (kind === 0x56503820) return [v.getUint16(26,true)&16383,v.getUint16(28,true)&16383];
            if (kind === 0x5650384c) return [1+((b[21]|(b[22]<<8))&16383),1+(((b[22]>>6)|(b[23]<<2)|(b[24]<<10))&16383)];
        }
        return null;
    }
    async function displayNative(img,url,policy,signal) {
        await nativeImage(img,url,policy,signal);
        if (!img.isConnected || signal.aborted) return;
        return drawThumbnail(img,img);
    }
    async function decode(img,url,policy,signal) {
        if (!lowResolution) {
            await nativeImage(img,url,policy,signal);
            return img;
        }
        const origin = new URL(url,location.href).origin;
        if (typeof createImageBitmap === 'function' && !blockedOrigins.has(origin)) {
            let response;
            try {response = await fetch(url,{mode:'cors',credentials:'omit',referrerPolicy:policy,signal});}
            catch (error) {
                if (signal.aborted) throw error;
                // Some source image hosts allow display but deny readable fetches.
                blockedOrigins.add(origin);
            }
            if (response) {
                if (!response.ok) {await response.body?.cancel();throw new Error(`Cover HTTP ${response.status}`);}
                const blob = await response.blob(), size = await dimensions(blob).catch(()=>null);
                let bitmap;
                try {
                    if (size?.[0] > 0 && size?.[1] > 0) {
                        const scale = Math.min(1,widthFor(img)/size[0],MAX_HEIGHT/size[1]);
                        bitmap = await createImageBitmap(blob,{resizeWidth:Math.max(1,Math.round(size[0]*scale)),
                            resizeQuality:'high'});
                        if (signal.aborted) throw new DOMException('Cover cancelled','AbortError');
                        return await drawThumbnail(img,bitmap);
                    }
                } catch (error) {
                    if (signal.aborted) throw error;
                    // Vector images and older decoders can use the display path.
                } finally {bitmap?.close();}
                const objectUrl = URL.createObjectURL(blob);
                let cover;
                try {
                    cover = await displayNative(img,objectUrl,policy,signal);
                    if (cover === img) objectUrls.set(img,objectUrl);
                    return cover;
                } finally {if (cover !== img) URL.revokeObjectURL(objectUrl);}
            }
        }
        // Drawing a cross-origin image is allowed even when canvas readback is
        // denied. Retain only the small canvas, then release the original image.
        return displayNative(img,url,policy,signal);
    }
    async function load(job,controller) {
        const {img,ready,missing,fallback} = job;
        const attempts = [[img.dataset.src,'no-referrer'],[img.dataset.src,'strict-origin-when-cross-origin']];
        if (fallback && fallback !== img.dataset.src) attempts.push([fallback,'no-referrer']);
        for (const [url,policy] of attempts) {
            try {
                const cover = await decode(img,url,policy,controller.signal);
                if (cover?.isConnected) {cover.dataset.originalSrc=url;ready(cover);}
                return;
            } catch (error) {if (controller.signal.aborted || !img.isConnected) return;}
        }
        if (img.isConnected) missing(img);
    }
    function pump() {
        while (active.size < CONCURRENCY && queue.length) {
            const job = queue.shift(), img = job.img;
            if (!img.isConnected) {img._coverQueued = false;continue;}
            const controller = new AbortController(); active.set(img,controller);
            load(job,controller).finally(()=>{active.delete(img);img._coverQueued=false;pump();});
        }
    }
    window.NovelCovers = {
        get lowResolutionEnabled() { return lowResolution; },
        setLowResolution(enabled) {
            lowResolution = !!enabled;
            try {localStorage.setItem('noveldb.lowResCovers',String(lowResolution));} catch (_) {}
            // Requeue visible covers through the same bounded loader. Cards
            // and their synopses keep their current positions and contents.
            for (const cover of document.querySelectorAll('.card-cover[data-src]')) {
                const options = cover._coverOptions || recoverable.get(cover);
                const img = document.createElement('img');
                img.className = 'card-cover'; img.dataset.src = cover.dataset.src;
                img.alt = cover.alt || ''; img.decoding = 'async';
                img._coverFallback = options?.fallback || cover._coverFallback;
                cover.parentElement?.classList.remove('cover-ready');
                cover.replaceWith(img);
            }
            this.prune();
        },
        load(img,options) {
            if (img._coverQueued || !img.isConnected) return;
            img._coverOptions = options;
            img._coverQueued = true; queue.push({img,...options}); pump();
        },
        prune() {
            for (const [img,url] of objectUrls) if (!img.isConnected) {URL.revokeObjectURL(url);objectUrls.delete(img);}
            for (let i=queue.length-1;i>=0;i--) if (!queue[i].img.isConnected) {queue[i].img._coverQueued=false;queue.splice(i,1);}
            for (const [img,controller] of active) if (!img.isConnected) controller.abort();
        },
    };
})();
