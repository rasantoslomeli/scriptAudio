"""Connected preparation ONLY. Download pinned assets, then transfer them offline."""
from pathlib import Path
import argparse
import json
import os
import shutil
import sys
import tarfile
import urllib.request
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from podcast_studio.edge import digest, write_json


def download_verified(asset,directory):
    target=Path(directory)/asset['filename']
    if target.is_file() and digest(target)==asset['sha256']:
        print('Already verified:',target.name,flush=True)
        return target
    temporary=target.with_name(target.name+'.partial')
    try:
        if urlsplit(asset['url']).scheme!='https':
            raise ValueError('Model preparation requires HTTPS')
        with urllib.request.urlopen(asset['url'],timeout=60) as source,temporary.open('wb') as output:
            if urlsplit(source.url).scheme!='https':
                raise ValueError('Non-HTTPS model redirect refused')
            total=int(source.headers.get('Content-Length',0));downloaded=0;reported=-1
            while data:=source.read(1024*1024):
                output.write(data);downloaded+=len(data)
                percentage=downloaded*100//total if total else downloaded//(64*1024*1024)
                if percentage//5!=reported:
                    reported=percentage//5
                    print(f"{target.name}: {downloaded//1048576} MiB"+(f" ({percentage}%)" if total else ''),flush=True)
        if digest(temporary)!=asset['sha256']:
            raise ValueError('Model checksum mismatch; no destination file was replaced')
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def prepare(profile,directory,transcript_only=False):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    catalog=json.loads((Path(__file__).parent/'model_catalog.json').read_text())
    selected=catalog['profiles'][profile]
    if transcript_only:selected=selected[:1]
    selected=selected+['segmentation','embedding']
    paths={}
    for name in selected:
        paths[name]=download_verified(catalog['assets'][name],directory)
    segmentation=directory/'segmentation.onnx'
    with tarfile.open(paths['segmentation'],'r:bz2') as archive:
        candidates=[member for member in archive.getmembers() if member.isfile() and Path(member.name).name=='model.onnx']
        if len(candidates)!=1:raise ValueError('Ambiguous segmentation archive; refusing extraction')
        temporary=segmentation.with_name(segmentation.name+'.partial')
        try:
            with archive.extractfile(candidates[0]) as source,temporary.open('wb') as output:shutil.copyfileobj(source,output)
            os.replace(temporary,segmentation)
        finally:temporary.unlink(missing_ok=True)
    receipt={'schema_version':1,'profile':profile,'asr_model':str(paths[selected[0]]),
             'segmentation_model':str(segmentation),'embedding_model':str(paths['embedding']),
             'assets':{name:catalog['assets'][name] for name in selected},'segmentation_sha256':digest(segmentation)}
    summary=[paths[name] for name in selected if name.startswith('summary_')]
    if summary:
        receipt['summary_model']=str(summary[0]);receipt['summary_shards']={p.name:digest(p) for p in summary[1:]}
    write_json(directory/'models.receipt.json',receipt)
    print('Models prepared. Build/register native engines and FFmpeg separately; review model licenses. Receipt:',directory/'models.receipt.json',flush=True)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=['rtx3080','apple-silicon','cpu'],required=True)
    parser.add_argument('--directory',required=True);parser.add_argument('--transcript-only',action='store_true')
    args=parser.parse_args();prepare(args.profile,args.directory,args.transcript_only)


if __name__=='__main__':main()
