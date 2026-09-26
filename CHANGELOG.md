# Changelog

## 1.1.0 (unreleased)

- Each release now includes `TwinRails.exe`, a single file that runs without
  Python, built by GitHub Actions with a SHA-256 checksum and a build
  provenance attestation.
- **Start automatically with Windows** works from the exe: the startup
  shortcut starts the exe itself.
- `--version` prints the version and exits.
- The README's install steps come first, and Risks and limits now covers
  Anthropic's policy on OAuth sign-in and session tokens.
- Added SECURITY.md, a bug report form, and CI that runs the tests on Windows.

## 1.0.0 (2026-09-25)

First public release.
