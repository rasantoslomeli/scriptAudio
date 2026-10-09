"""Run on each target OS: PyInstaller does not cross-compile Qt applications."""
from pathlib import Path
import os
import argparse
import platform
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--edge-bundle', help='Prepared offline directory, containing edge-config.json, engines, models and licenses')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
system = platform.system()
command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
           "--name", "PodcastSpeakerStudio", "--paths", str(root), "--collect-data", "reportlab",
           "--collect-all", "sherpa_onnx",
           "--add-data", f"{root / 'samples'}{os.pathsep}samples", str(root / "scripts" / "launcher.py")]
if args.edge_bundle:
    sys.path.insert(0,str(root))
    from podcast_studio.edge import EdgeConfig
    bundle = Path(args.edge_bundle).resolve()
    config = EdgeConfig.load(bundle/'edge-config.json').verify(summary=True)
    for name in config.asset_names():
        if not Path(getattr(config,name)).is_relative_to(bundle):
            raise ValueError('Embedded offline assets must all be inside the bundle folder')
    for file in bundle.rglob('*'):
        if not file.resolve().is_relative_to(bundle):
            raise ValueError('Bundle symlink escapes its folder')
    command += ['--add-data',f'{bundle}{os.pathsep}edge-bundle']
if system == "Darwin":
    command += ["--osx-bundle-identifier", "com.podcastspeakerstudio.desktop"]
subprocess.run(command, cwd=root, check=True)
if system == "Darwin":
    # ditto preserves symlinks and bundle metadata; sign/notarize before distributing publicly.
    archive = root / "dist" / f"PodcastSpeakerStudio-macOS-{platform.machine()}.zip"
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                    str(root / "dist" / "PodcastSpeakerStudio.app"), str(archive)], check=True)
else:
    archive = shutil.make_archive(str(root / "dist" / f"PodcastSpeakerStudio-{system}-{platform.machine()}"),
                                  "zip", root_dir=root / "dist", base_dir="PodcastSpeakerStudio")
print(f"Native {system} package: {archive}")
