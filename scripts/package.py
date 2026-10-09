"""Run on each target OS: PyInstaller does not cross-compile Qt applications."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
system = platform.system()
command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
           "--name", "PodcastSpeakerStudio", "--paths", str(root), "--collect-data", "reportlab",
           "--add-data", f"{root / 'samples'}{os.pathsep}samples", str(root / "scripts" / "launcher.py")]
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
