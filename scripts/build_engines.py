"""Build pinned whisper.cpp and llama.cpp on the target OS, not a cross-compiler."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

# Fixed upstream revisions; SHA256 registration pins the resulting native artifacts too.
WHISPER_REVISION='d1be6fde11ac6e0407606b4e42fe72d34add8037'
# Set by inspection of the official upstream checkout before delivery.
LLAMA_REVISION='79e2e74eb11022c1ba2e438df7f0ca2d4c10f8b6'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=['rtx3080','apple-silicon','cpu'],required=True)
    parser.add_argument('--directory',required=True)
    parser.add_argument('--jobs',type=int,default=min(4,os.cpu_count() or 1))
    args=parser.parse_args()
    if args.jobs<1 or args.jobs>32:
        parser.error('jobs must be between 1 and 32')
    if args.profile=='apple-silicon' and (sys.platform!='darwin' or __import__('platform').machine()!='arm64'):
        parser.error('The Metal target requires an Apple Silicon macOS build host')
    cmake=shutil.which('cmake')
    if not cmake:
        candidate=Path(sys.executable).parent/('cmake.exe' if sys.platform=='win32' else 'cmake')
        cmake=str(candidate) if candidate.is_file() else None
    if not cmake:
        parser.error('Install the development tools (uv sync --frozen --extra dev)')
    root=Path(args.directory).resolve();root.mkdir(parents=True,exist_ok=True)
    for name,revision,target in [('whisper.cpp',WHISPER_REVISION,'whisper-cli'),('llama.cpp',LLAMA_REVISION,'llama-server')]:
        source=root/name
        if not source.exists():
            source.mkdir(parents=True)
            subprocess.run(['git','init',str(source)],check=True)
            subprocess.run(['git','-C',str(source),'remote','add','origin','https://github.com/ggml-org/'+name+'.git'],check=True)
            subprocess.run(['git','-C',str(source),'fetch','--depth','1','origin',revision],check=True)
        # Only manipulate checkouts owned by this engine build directory; never the application checkout.
        if subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip():
            raise RuntimeError('Engine checkout has local modifications; preserve/review them before building')
        subprocess.run(['git','-C',str(source),'checkout','--detach',revision],check=True)
        build=source/'build'
        command=[cmake,'-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DBUILD_SHARED_LIBS=OFF','-DGGML_NATIVE=OFF',
                 '-DGGML_CUDA='+('ON' if args.profile=='rtx3080' else 'OFF'),
                 '-DGGML_METAL='+('ON' if args.profile=='apple-silicon' else 'OFF')]
        if args.profile=='rtx3080':
            command+=['-DCMAKE_CUDA_ARCHITECTURES=86']
        if args.profile=='apple-silicon':
            command+=['-DGGML_METAL_EMBED_LIBRARY=ON']
        if name=='llama.cpp':
            command+=['-DLLAMA_BUILD_TESTS=OFF']
        else:
            command+=['-DWHISPER_BUILD_TESTS=OFF']
        subprocess.run(command,check=True)
        subprocess.run([cmake,'--build',str(build),'--config','Release','--target',target,'-j',str(args.jobs)],check=True)
        print('Built',target,'for',args.profile,'in',build)


if __name__=='__main__':
    main()
