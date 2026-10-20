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
    raw = source_names.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    compressed = gzip.compress(raw,compresslevel=9,mtime=0)
    name = f'{source}_chunk_preview.json.gz'
    atomic_bytes(output_dir / name,compressed)
    manifest_path = output_dir / f'{source}_chunk_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.update(previewUrl=name,previewHash=hashlib.sha256(compressed).hexdigest())
    atomic_json(manifest_path,manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=SOURCES,action='append')
    parser.add_argument('--output-dir',default='docs/data')
    args = parser.parse_args()
    for source in args.source or SOURCES:
        manifest = build(source,args.output_dir)
        print(f'{source}: {manifest["previewUrl"]}')
