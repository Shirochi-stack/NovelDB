"""Publish small, exact ranking previews from the final catalog chunks."""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import subprocess

try:
    from . import source_names
    from .metadata_common import atomic_bytes, atomic_json
except ImportError:
    import source_names
    from metadata_common import atomic_bytes, atomic_json

SOURCES = ('npia','kpage','sfc','nweb','jara','mpia','rbooks','nseries')


def build(source, output_dir):
    if source not in SOURCES:
        raise ValueError('Unknown preview source')
    output_dir = Path(output_dir).resolve()
    result = subprocess.run(['node',str(Path(__file__).with_name('catalog_preview.cjs')),source,str(output_dir)],
                            check=True,capture_output=True,encoding='utf-8')
    payload = json.loads(result.stdout)
    name = f'{source}_chunk_preview.json.gz'
    manifest_path = output_dir / f'{source}_chunk_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    expanded_name = f'{source}_chunk_preview_sorts.json.gz'
    def write_asset(filename,value):
        raw = source_names.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        compressed = gzip.compress(raw,compresslevel=9,mtime=0)
        atomic_bytes(output_dir / filename,compressed)
        return hashlib.sha256(compressed).hexdigest()
    # Keep the default ranking download small. All other sorts warm in the
    # background and reuse one compact dictionary of their selected rows.
    primary = {'npia':['daily','weekly','monthly'],'nweb':['daily','weekly'],
               'jara':['daily','weekly','monthly'],'mpia':['daily'],'rbooks':['weekly','monthly']}.get(source)
    if primary is None:
        primary = ['sfc_popularity','sfc_bestseller','sfc_newbooks','sfc_bookmarks','sfc_jp','sfc_ticket'] if source=='sfc' else (
            [f'rank:{source}:{next(iter(manifest["boards"]))}'] if manifest.get('boards') else ['updated'])
    default = {key:payload[key] for key in ('source','total','chunkHashes','limit')}
    default.update(version=1,novels=[],ordinals=[],translations={},profiles={})
    selected = {}
    for sort in primary:
        positions = []
        for index in payload['profiles'][json.dumps([sort,'desc'],separators=(',',':'))]:
            if index not in selected:
                selected[index] = len(default['novels'])
                entry = payload['novels'][index]
                default['novels'].append(entry);default['ordinals'].append(payload['ordinals'][index])
                if str(entry[0]) in payload['translations']:default['translations'][str(entry[0])] = payload['translations'][str(entry[0])]
            positions.append(selected[index])
        default['profiles'][sort] = positions
    manifest.update(previewUrl=name,previewHash=write_asset(name,default),
                    previewSortsUrl=expanded_name,previewSortsHash=write_asset(expanded_name,payload))
    atomic_json(manifest_path,manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=SOURCES,action='append')
    parser.add_argument('--output-dir',default='docs/data')
    args = parser.parse_args()
    for source in args.source or SOURCES:
        manifest = build(source,args.output_dir)
        print(f'{source}: {manifest["previewUrl"]}',flush=True)
