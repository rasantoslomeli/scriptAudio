# Native packaging and signing

Use a native Python 3.12 installation of the target architecture. Install locked dependencies with `uv sync --frozen --extra dev`, run tests, then `uv run --frozen --extra dev python scripts/package.py`. Samples and ReportLab fonts are included. Qt multimedia codecs come from PySide6; external ffprobe is optional and is not bundled.

## Windows 11

Build on Windows 11 or the provided `windows-2025` CI runner. Output:

- `dist/PodcastSpeakerStudio/PodcastSpeakerStudio.exe` with its required `_internal` files.
- `dist/PodcastSpeakerStudio-Windows-AMD64.zip` (architecture string can vary).

Distribute the whole folder/ZIP, not the executable alone. This is a portable desktop executable, not an MSI installer. Run it on a clean Windows 11 machine, test import of each advertised format, cloud transcription, resume, playback, exports, and non-ASCII file paths. Code signing with your organization's certificate reduces SmartScreen warnings. For example, in a configured Windows SDK environment:

```powershell
signtool sign /a /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 dist\PodcastSpeakerStudio\PodcastSpeakerStudio.exe
signtool verify /pa dist\PodcastSpeakerStudio\PodcastSpeakerStudio.exe
```

Recreate the ZIP after signing. Certificate access is managed by your organization; do not put signing secrets in source or project files.

## macOS Tahoe (26)

Build with the provided `macos-26` CI runner or a Tahoe desktop. Output: `dist/PodcastSpeakerStudio.app` and a ZIP. The build uses the current runner's architecture; it is **not** a universal Intel/Apple Silicon bundle. Build each supported architecture with matching Python and dependencies, and test each on its native hardware.

Public distribution needs a paid Apple Developer account, Developer ID Application identity, hardened runtime, and notarization. Use an appropriate PyInstaller native signing configuration (`--codesign-identity` and a reviewed `--osx-entitlements-file`) so nested Qt frameworks and binaries are signed inside-out. Test Qt multimedia and Python extension loading under the chosen entitlements. Do not blindly apply broad entitlements or ad-hoc signatures and call the app notarized.

After a correctly signed native build:

```sh
codesign --verify --deep --strict --verbose=2 dist/PodcastSpeakerStudio.app
ditto -c -k --sequesterRsrc --keepParent dist/PodcastSpeakerStudio.app dist/PodcastSpeakerStudio-notarize.zip
xcrun notarytool submit dist/PodcastSpeakerStudio-notarize.zip --keychain-profile YOUR_NOTARY_PROFILE --wait
xcrun stapler staple dist/PodcastSpeakerStudio.app
xcrun stapler validate dist/PodcastSpeakerStudio.app
spctl --assess --type execute --verbose=2 dist/PodcastSpeakerStudio.app
```

Recreate the distribution archive after stapling. Configure the notary profile securely with `notarytool store-credentials`; no signing or notarization credentials are included here. Signing/notarization has **not** been performed in the Linux cloud environment.

## Native build automation

`.github/workflows/desktop-builds.yml` tests and packages on Windows and macOS for manual dispatch, version tags, and pull requests. It uploads ZIP artifacts; it does not sign them or publish a release. The workflow has not run merely because its YAML exists. Push the reviewed project to GitHub and trigger the workflow to obtain target-native artifacts. Windows Server CI coverage does not replace Windows 11 desktop acceptance testing.

## Linux artifact

The cloud build produces `dist/PodcastSpeakerStudio-Linux-x86_64.zip`. This validates the PyInstaller pipeline for Linux only. A headless launch can validate initialization and decoding with `QT_QPA_PLATFORM=offscreen QT_AUDIO_BACKEND=pulseaudio`; Linux desktops need their usual display/audio libraries. Linux executable compatibility depends on the build host's glibc and system libraries.

Before distributing, review dependency licenses, codec licensing, and Qt LGPL obligations for your distribution. This project keeps PyInstaller's Qt libraries as dynamically linked files in an onedir bundle. Include corresponding license notices/source offers as required; a zipped development build is not a completed legal distribution review.
