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
