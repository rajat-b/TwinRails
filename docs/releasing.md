# Releasing

Releases are built by [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).
Every push runs the tests on Windows (Python 3.10 and 3.14) and builds
`TwinRails.exe` with PyInstaller. The exe is uploaded as a workflow artifact,
so you can download it from the run's summary page and try it before tagging.

## Cutting a release

1. Set `__version__` in `main.py` to the new version, and move the
   `CHANGELOG.md` entry from "unreleased" to today's date.
2. Commit that to `master`, wait for CI to pass, and try the exe from that
   run's artifact on a real taskbar, e.g. with `--demo` to fill the bars with
   sample data.
3. Tag and push: `git tag v1.1.0 && git push origin v1.1.0`.

The tag run repeats the tests and build, checks the tag matches
`__version__` (it stops if they differ), then creates the GitHub release and
attaches `TwinRails.exe` and `SHA256SUMS.txt`. If a release for that tag
already exists, the files are added to it instead.

## Things the build depends on

- **Hidden imports.** pystray chooses its backend with `importlib` when it is
  imported, and curl_cffi ships a native library, so the build names
  `pystray._win32` and collects all of `curl_cffi` explicitly. If a new
  dependency does something similar, the smoke test fails: it runs
  `TwinRails.exe --version`, which exits right after `main.py`'s imports.
- **Frozen paths.** In the one-file exe, `__file__` points into a temporary
  folder that is deleted when the app exits. Anything that must outlive the
  process, like the Startup shortcut in `src/core/config.py`, uses
  `sys.executable` when `sys.frozen` is set.
- **The icon.** `packaging/twinrails.ico` is `docs/logo.svg` rendered at
  16–256px. Regenerate it if the mark changes.
- **Unsigned exe.** SmartScreen warns about unsigned downloads until they
  build a reputation. Free code signing for open-source projects (for example
  the SignPath Foundation) would remove that warning.
