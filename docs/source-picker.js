/* Source icons with keyboard navigation and the existing select contract. */
(function () {
    'use strict';
    const select = document.getElementById('sourceSelect'), host = select.closest('.source-control');
    const icons = {all:'halgakos.ico',npia:'icons/npia.png',kpage:'icons/kpage.png',sfc:'icons/sfc.ico',
        nweb:'icons/nweb.png',jara:'icons/jara.ico',mpia:'icons/mpia.ico',rbooks:'icons/rbooks.png',nseries:'icons/nseries.png'};
    const button = document.createElement('button'), menu = document.createElement('div');
    button.type = 'button'; button.id = 'sourcePickerButton'; button.className = 'source-picker-button';
    button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls','sourcePickerMenu');
    menu.id = 'sourcePickerMenu';menu.className='source-picker-menu';menu.hidden=true;menu.setAttribute('role','listbox');menu.setAttribute('aria-label','Sources');
    const selectedIcon = document.createElement('img'), selectedText = document.createElement('span');
    selectedIcon.alt='';selectedIcon.width=selectedIcon.height=20;button.append(selectedIcon,selectedText);
    const items = new Map();
    for (const option of select.options) {
        const item = document.createElement('button'), icon = document.createElement('img'), text = document.createElement('span');
        item.type='button';item.className='source-picker-option';item.tabIndex=-1;item.setAttribute('role','option');item.dataset.source=option.value;
        icon.src=icons[option.value]||icons.all;icon.alt='';icon.width=icon.height=20;text.textContent=option.textContent;
        item.append(icon,text);menu.append(item);items.set(option.value,item);
        item.addEventListener('click',()=>{
            if (option.disabled) return;
            select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));close(true);
        });
    }
    function sync() {
        const chosen = select.selectedOptions[0] || select.options[0];
        selectedIcon.src=icons[chosen.value]||icons.all;selectedText.textContent=chosen.textContent;
        button.setAttribute('aria-label',`Source: ${chosen.textContent}`);
        for (const option of select.options) {
            const item=items.get(option.value);if(!item)continue;
            item.disabled=option.disabled;item.title=option.title;item.setAttribute('aria-selected',String(option.value===select.value));
        }
    }
    function close(focus=false) {menu.hidden=true;button.setAttribute('aria-expanded','false');if(focus)button.focus();}
    function open() {
        sync();menu.hidden=false;button.setAttribute('aria-expanded','true');
        (items.get(select.value)||[...items.values()].find(item=>!item.disabled))?.focus();
    }
    button.addEventListener('click',()=>menu.hidden?open():close());
    button.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();open();}});
    menu.addEventListener('keydown',event=>{
        if(event.key==='Escape'){event.preventDefault();close(true);return;}
        if(event.key==='Tab'){close();return;}
        const enabled=[...items.values()].filter(item=>!item.disabled), at=enabled.indexOf(document.activeElement);
        let next;
        if(event.key==='ArrowDown')next=(at+1)%enabled.length;
        if(event.key==='ArrowUp')next=(at-1+enabled.length)%enabled.length;
        if(event.key==='Home')next=0;if(event.key==='End')next=enabled.length-1;
        if(next!=null){event.preventDefault();enabled[next]?.focus();}
    });
    document.addEventListener('pointerdown',event=>{if(!host.contains(event.target))close();});
    select.addEventListener('change',sync);
    new MutationObserver(sync).observe(select,{subtree:true,attributes:true,childList:true,characterData:true});
    select.classList.add('source-select-native');select.tabIndex=-1;select.setAttribute('aria-hidden','true');
    host.querySelector('label').htmlFor=button.id;host.append(button,menu);sync();
    window.NovelSourcePicker={sync,iconFor:source=>icons[source] || icons.all};
})();
