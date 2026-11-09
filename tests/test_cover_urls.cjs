const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = {URL,addEventListener:()=>{},performance,localStorage:{getItem:()=>null,setItem:()=>{}},
    window:{addEventListener:()=>{}},document:{addEventListener:()=>{}}};
vm.runInNewContext(fs.readFileSync(require.resolve('../docs/cover-loader.js'),'utf8'),context);
const urls = context.window.NovelCovers.sourceUrls;

test('cover upgrades preserve resource keys, other parameters, and a working fallback',()=>{
    const host='cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com';
    const cases=[
        ['sfc','https://rss.\u0073\u0066\u0061\u0063\u0067.com/web/novel/images/NovelCover/Small/2026/10/id.jpg?v=2','https://rss.\u0073\u0066\u0061\u0063\u0067.com/web/novel/images/NovelCover/Big/2026/10/id.jpg?v=2'],
        ['rbooks','https://img.\u0072\u0069\u0064\u0069cdn.net/cover/3049085629/large?v=2&dpi=xhdpi#1','https://img.\u0072\u0069\u0064\u0069cdn.net/cover/3049085629/xxlarge?v=2&dpi=xxhdpi#1'],
        ['npia','https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/imagebox/cover/id_123_ori.wimg?v=2','https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/imagebox/cover/id_123_ori.file?v=2'],
        ['mpia',`https://${host}/v2/files/tcover/2026/1020/21/id`,`https://${host}/v2/files/tcover/2026/1020/21/id.origin.`],
        ['nweb','https://novel-phinf.pstatic.net/image.png?type=f280_224_2&v=2','https://novel-phinf.pstatic.net/image.png?v=2'],
        ['nseries','https://comicthumb-phinf.pstatic.net/image.jpg?type=m79','https://comicthumb-phinf.pstatic.net/image.jpg'],
        ['kpage','https://dn-img-page.\u006b\u0061\u006b\u0061\u006f.com/download/resource?kid=a+b/c&filename=th3','https://dn-img-page.\u006b\u0061\u006b\u0061\u006f.com/download/resource?kid=a+b/c'],
    ];
    for(const [source,stored,preferred] of cases){
        assert.equal(urls(stored,source).src,preferred);
        assert.equal(urls(stored,source).fallback,stored);
    }
    const original=`https://${host}/v2/files/cover/2026/1020/21/id.origin.`;
    assert.equal(urls(original,'mpia').src,original);
    assert.equal(urls(original,'mpia').fallback,original.slice(0,-8));
});

test('unrelated hosts, sources, placeholders and invalid URLs are left alone',()=>{
    for(const [source,value] of [
        ['sfc','https://rss.\u0073\u0066\u0061\u0063\u0067.com/web/novel/images/NovelCover/Big/2026/10/id.jpg'],
        ['jara','https://cf-image.\u006a\u006f\u0061\u0072\u0061.com/literature_file/id_thumb.png'],
        ['rbooks','https://img.\u0072\u0069\u0064\u0069cdn.net/cover/123/xxlarge?dpi=xxhdpi'],
        ['rbooks','https://covers.test/cover/123/large'],
        ['rbooks','https://img.\u0072\u0069\u0064\u0069cdn.net.evil.test/cover/123/large'],
        ['rbooks','https://img.\u0072\u0069\u0064\u0069cdn.net/assets/logo.png'],
        ['rbooks','https://img.\u0072\u0069\u0064\u0069cdn.net/cover/123/unknown'],
        ['rbooks','https://user:pass@img.\u0072\u0069\u0064\u0069cdn.net/cover/123/large'],
        ['npia','https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/imagebox/cover/id_123_ori.file'],
        ['npia','https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/img/layout/readycover4.png'],
        ['npia','https://covers.test/imagebox/cover/id_123_ori.wimg'],
        ['mpia','https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/imagebox/cover/id_123_ori.wimg'],
        ['mpia','https://covers.test/v2/files/tcover/id'],
        ['mpia','https://cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/v2/attachment/covers/default.jpg'],
        ['npia','https://novel-phinf.pstatic.net/image.png?type=m79'],
        ['nweb','https://novel-phinf.pstatic.net.evil.test/image.png?type=m79'],
        ['nweb','javascript:alert(1)'],['nweb',''],
    ]){
        assert.equal(urls(value,source).src,value);
        assert.equal(urls(value,source).fallback,'');
    }
});

test('rbooks cover size aliases use the full-size resource and retain the asset ID',()=>{
    const base='https://img.\u0072\u0069\u0064\u0069cdn.net/cover/3049085629';
    for(const suffix of ['', '/', '/small', '/large', '/xlarge', '/xxlarge', '/large/']) {
        const stored=base+suffix;
        assert.equal(urls(stored,'rbooks').src,base+'/xxlarge?dpi=xxhdpi');
        assert.equal(urls(stored,'rbooks').fallback,stored);
    }
});
