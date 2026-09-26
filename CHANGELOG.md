# Changelog

## 1.1.0 (2026-09-26)

- Each release now includes `TwinRails.exe`, a single file that runs without
  Python, built by GitHub Actions with a SHA-256 checksum and a build
  provenance attestation.
- **Start automatically with Windows** works from the exe: the startup
  shortcut starts the exe itself.
- `--version` prints the version and exits.
- The logo's empty rail is hollow, like the bars, and the taskbar mark takes
  the rows' light or dark theme colours instead of fixed dark greys.
- The README's install steps come first, Risks and limits now covers
  Anthropic's policy on OAuth sign-in and session tokens, and a Maintenance
  section says what to expect from issues.
- Added SECURITY.md, a bug report form, and CI that runs the tests on Windows.

## 1.0.0 (2026-09-25)

First public release.
