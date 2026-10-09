"""Separate preparation tool; the application never downloads models.

Register trusted local files into a checksum manifest, verify an air-gap bundle,
or download one HTTPS asset when its independently obtained SHA256 is known.
"""
from pathlib import Path
import argparse
import dataclasses
import os
import shutil
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from podcast_studio.edge import EdgeConfig, PROFILES, digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    register=commands.add_parser('register')
    register.add_argument('--profile',choices=PROFILES,required=True)
    register.add_argument('--output',required=True)
    for name in EdgeConfig.asset_names():
        register.add_argument('--'+name.replace('_','-'),required=name in EdgeConfig.asset_names()[:5])
    register.add_argument('--threads',type=int)
    register.add_argument('--cluster-threshold',type=float,default=.4)
    verify=commands.add_parser('verify');verify.add_argument('config');verify.add_argument('--summaries',action='store_true')
    download=commands.add_parser('download');download.add_argument('--url',required=True);download.add_argument('--sha256',required=True);download.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='register':
        target=Path(args.output).resolve()
        values={name:str(Path(getattr(args,name)).resolve()) if getattr(args,name) else '' for name in EdgeConfig.asset_names()}
        config=EdgeConfig(args.profile,**values,threads=args.threads or PROFILES[args.profile]['threads'],cluster_threshold=args.cluster_threshold)
        config.hashes={name:digest(path) for name,path in values.items() if path}
        config.verify(summary=bool(config.summary_model))
        # Relative paths make a complete directory tree transferable to an air-gapped host.
        for name,path in values.items():
            if path:
                setattr(config,name,os.path.relpath(path,target.parent))
        config.save(target)
        print('Registered local asset integrity; source authenticity must be established before registration:',target)
    elif args.command=='verify':
        config=EdgeConfig.load(args.config).verify(summary=args.summaries)
        print('Offline bundle verified:',config.profile)
    else:
        from urllib.parse import urlsplit
        parsed=urlsplit(args.url)
        if parsed.scheme!='https' or parsed.username or parsed.password or len(args.sha256)!=64:
            parser.error('Use a credential-free HTTPS URL and an independently obtained full SHA256')
        target=Path(args.output).resolve();target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name(target.name+'.partial')
        try:
            with urllib.request.urlopen(args.url,timeout=60) as source,temporary.open('wb') as output:
                if urlsplit(source.url).scheme!='https':
                    raise ValueError('Refusing a non-HTTPS redirect')
                shutil.copyfileobj(source,output)
            if digest(temporary)!=args.sha256.lower():
                raise ValueError('Downloaded asset checksum does not match; original destination preserved')
            os.replace(temporary,target)
        finally:
            temporary.unlink(missing_ok=True)
        print('Verified download:',target)


if __name__=='__main__':
    main()
