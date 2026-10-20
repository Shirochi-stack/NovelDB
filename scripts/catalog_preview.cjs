// Use the browser's comparator to publish exact catalog preview prefixes.
const fs = require('node:fs'), path = require('node:path'), zlib = require('node:zlib');
const M = require('../docs/metadata-core.js');
const [source,directory] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync(path.join(directory,`${source}_chunk_manifest.json`),'utf8'));
const cfg = {format:manifest.format || 'array',sfcRanks:source==='sfc'};
const sorts = M.previewSorts(source,manifest.boards || {});
const collator = new Intl.Collator('en');
function selection(options) {
    const plan = M.sortPlan(options), heap = [];
    function compare(a,b) {
        for (let i=0;i<plan.length;i++) {
            const key=plan[i],x=a.keys[i],y=b.keys[i];
            if (key.nullsLast && (x==null || y==null)) {
                if (x==null && y==null) continue;
                return x==null ? 1 : -1;
            }
            const delta = key.text==='collate' ? collator.compare(String(x),String(y))
                : key.text==='plain' ? (String(x)<String(y) ? -1 : String(x)>String(y) ? 1 : 0) : Number(x)-Number(y);
            if (delta) return delta*key.dir;
        }
        return a.record._catalogOrdinal-b.record._catalogOrdinal;
    }
    return {heap,options,add(record) {
        const entry={record,keys:plan.map(key=>key.get(record))};
        if (heap.length<300) {
            let at=heap.length;heap.push(entry);
            while (at) {const parent=(at-1)>>1;if(compare(heap[parent],entry)>=0)break;heap[at]=heap[parent];at=parent;}
            heap[at]=entry;
        } else if (compare(entry,heap[0])<0) {
            let at=0;
            while (at*2+1<heap.length) {
                let child=at*2+1;
                if (child+1<heap.length && compare(heap[child+1],heap[child])>0)child++;
                if (compare(entry,heap[child])>=0)break;
                heap[at]=heap[child];at=child;
            }
            heap[at]=entry;
        }
    }};
}
const best = new Map();
for (const sortBy of sorts) for (const order of ['desc','asc']) {
    best.set(JSON.stringify([sortBy,order]),selection({sortBy,order,audience:'all',defaultSource:source}));
}
let ordinal = 0;
for (const file of manifest.files) {
    const payload = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(directory,file))).toString('utf8'));
    const rows = payload.novels || payload, translations = payload.translations || {};
    const records = M.parseRows(rows,source,cfg);
    records.forEach((record,index)=>{
        record._catalogOrdinal = ordinal++;
        record._row = rows[index];
        record.titleEn = translations[record.id] || '';
    });
    for (const profile of best.values()) for (const record of records) profile.add(record);
}
const novels = [], translations = {}, ordinals = [], positions = new Map(), profiles = {};
for (const [key,profile] of best) {
    // The heap only selects candidates; the shared final sort also preserves
    // catalog ordinals for equal values and absent metrics.
    const records=profile.heap.map(entry=>entry.record).sort((a,b)=>a._catalogOrdinal-b._catalogOrdinal);
    M.sortRecords(records,profile.options);
    profiles[key] = records.map(record=>{
        if (!positions.has(record.id)) {
            positions.set(record.id,novels.length);
            const row = record._row.slice();
            if (source==='sfc' && row.length>17) row[17]='';
            novels.push(row); ordinals.push(record._catalogOrdinal);
            if (record.titleEn) translations[record.id]=record.titleEn;
        }
        return positions.get(record.id);
    });
}
process.stdout.write(JSON.stringify({version:2,source,total:manifest.totalEntries,
    chunkHashes:manifest.chunkHashes,limit:300,novels,translations,ordinals,profiles}));
