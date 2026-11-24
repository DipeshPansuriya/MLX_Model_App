---
type: ADR
title: "ADR-007 — Ad-hoc code signature for distribution"
status: "Accepted, revisit"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../DEPLOYMENT.md
  - ../SECURITY.md
---

# ADR-007 — Ad-hoc code signature for distribution

## 1. Decision

The `.app` bundle is signed ad-hoc (`codesign --force --deep --sign -`) and
distributed in a DMG without notarisation. Because a browser download carries
`com.apple.quarantine` and macOS 15+ removed the Control-click → Open bypass, the
primary install path is `scripts/install.sh`, which downloads with `curl` (no
quarantine flag), verifies the SHA-256 and the signature, then clears quarantine
on the installed copy. The DMG stays available for people who prefer it, with the
`xattr` step documented.

## 2. Context

macOS Gatekeeper blocks unsigned and unnotarised applications downloaded from the
internet. The alternatives are: ship unsigned, sign ad-hoc, or sign with a
Developer ID and notarise. The last requires a paid Apple Developer account and a
notarisation round trip through Apple's service on every release.

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Ship unsigned** | `codesign --verify` fails outright; some macOS versions refuse to launch the bundle at all |
| **Developer ID + notarisation** | The correct end state, but needs a paid account, a certificate in the keychain, and a network round trip per release; not available for this release |
| **Self-signed certificate** | Tested: `codesign --verify` passes and the Authority shows, but `spctl` still returns `rejected` with `origin=…`, and trusting the certificate in the login keychain does not change that. No better than ad-hoc for Gatekeeper |
| **Homebrew cask** | Tested: `brew install --cask` still applies quarantine and the app is rejected (`Gatekeeper rejection` in the log). `HOMEBREW_CASK_OPTS=--no-quarantine` was ignored, and a `quarantine false` stanza is not a valid cask directive (`undefined method 'quarantine'`). Homebrew also refuses untrusted taps without `brew trust` |
| **Ad-hoc + curl installer (chosen)** | Produces a valid signature, costs nothing, needs no account, and `curl` sets no quarantine flag so the prompt never appears |
| **Ad-hoc + DMG (kept)** | Works once `xattr -dr com.apple.quarantine` is applied to the DMG before installing; documented as the secondary route |

## 4. Consequences

**Good**

- `codesign --verify --deep --strict` passes, so bundle integrity can be checked
  and the signature seals the resources.
- No account, no certificate, no notarisation wait in the release loop.
- The DMG is reproducible on any Mac with the Xcode command line tools.
- The curl installer gives a prompt-free install without paying Apple, and it
  verifies the SHA-256 and the signature before installing.

**Bad**

- A **browser** download still triggers "Apple could not verify…". The user must
  clear quarantine on the DMG before installing, allow it once through System
  Settings → Privacy & Security → **Open Anyway** (macOS 15+; the right-click →
  Open shortcut was removed in Sequoia), or use the installer. This is a real
  friction point for a non-technical user and is documented in the README and
  [RUNBOOK.md](../RUNBOOK.md) §4.7.
- No team identifier, so the signature proves integrity but not authorship.
- No hardened runtime, no entitlements; the app runs with the interpreter's
  permissions.
- `--deep` is deprecated for signing in Apple's guidance; it is used here because
  the bundle contains no nested code beyond the stub.

## 5. Revisit trigger

Obtain a Developer ID and notarise before distributing to users who will not read
install instructions. This is recorded as OD-1 in [PRD.md](../PRD.md) §9.

## 6. Evidence

| Claim | Location |
|---|---|
| Signing step | `packaging/build_app.sh` (`codesign --force --deep --sign -`) |
| Verification result | `docs/TESTING.md` §4 |
| Prompt-free installer | `scripts/install.sh`; SA-16 in `scripts/security_audit.sh` |
| User-facing instruction | `README.md` install section; `RUNBOOK.md` §4.7 |
| Open decision | `docs/PRD.md` §9 OD-1 |

## 7. Measurements

Taken on macOS 27.2.1 against `MLX.Studio-2.0.1.dmg`, checksum
`36404a17…`:

| Route | Quarantine on installed app | `spctl -a -t exec` | Launch |
|---|---|---|---|
| Chrome download → drag → double-click | `0081;…;Chrome;…` | `rejected` | blocked, `ASP: Security policy would not allow process` |
| Same, then `xattr -dr` on the `.app` | cleared | `rejected` | runs |
| `xattr -dr` on the `.dmg`, then install | cleared | `rejected` | runs |
| `curl -L -o` the DMG, then install | cleared | `rejected` | runs |
| `scripts/install.sh` | cleared | `rejected` | runs |
| `brew install --cask` from a tap | `0181;…` | `rejected` | blocked |
| Self-signed cert, trusted in login keychain | n/a | `rejected`, `origin=MLX Studio Self Signed` | blocked |

`spctl` rejects in every case: an ad-hoc or self-signed signature is not a
distribution identity. What decides whether the app launches is the quarantine
attribute, not the `spctl` verdict.
