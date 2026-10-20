// Use the browser's comparator to publish exact first-three-page previews.
const fs = require('node:fs'), path = require('node:path'), zlib = require('node:zlib');
const M = require('../docs/metadata-core.js');
const [source,directory] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync(path.join(directory,`${source}_chunk_manifest.json`),'utf8'));
const cfg = {format:manifest.format || 'array',sfcRanks:source==='sfc'};
const periods = M.supportedPeriods(source);
const sorts = periods.length ? periods : source==='sfc'
    ? ['sfc_popularity','sfc_bestseller','sfc_newbooks','sfc_bookmarks','sfc_jp','sfc_ticket']
    : Object.keys(manifest.boards || {}).length ? [`rank:${source}:${Object.keys(manifest.boards)[0]}`] : ['updated'];
const best = new Map(sorts.map(sort=>[sort,[]]));
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
    for (const sort of sorts) {
        const candidates = best.get(sort).concat(records);
        M.sortRecords(candidates,{sortBy:sort,order:'desc',audience:'all',defaultSource:source});
        best.set(sort,candidates.slice(0,300));
    }
}
const novels = [], translations = {}, ordinals = [], positions = new Map(), profiles = {};
for (const [sort,records] of best) {
    profiles[sort] = records.map(record=>{
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
process.stdout.write(JSON.stringify({version:1,source,total:manifest.totalEntries,
    chunkHashes:manifest.chunkHashes,limit:300,novels,translations,ordinals,profiles}));
