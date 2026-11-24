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
distributed in a DMG without notarisation. On first launch macOS refuses it; the
user allows it once via **System Settings → Privacy & Security → Open Anyway**
(macOS 15+) or right-click → **Open** (macOS 14 and older), or clears the
quarantine attribute with `xattr -dr com.apple.quarantine`.

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
| **Self-signed certificate** | No better than ad-hoc for Gatekeeper; adds a certificate to manage |
| **Ad-hoc (chosen)** | Produces a valid signature that satisfies integrity checks, costs nothing, and needs no account |

## 4. Consequences

**Good**

- `codesign --verify --deep --strict` passes, so bundle integrity can be checked
  and the signature seals the resources.
- No account, no certificate, no notarisation wait in the release loop.
- The DMG is reproducible on any Mac with the Xcode command line tools.

**Bad**

- Gatekeeper still warns on first launch: "Apple could not verify…". The user must
  allow it once through System Settings → Privacy & Security → **Open Anyway**
  (macOS 15+; the right-click → Open shortcut was removed in Sequoia), or clear
  the quarantine attribute. This is a real friction point for a non-technical user
  and is documented in the README and [RUNBOOK.md](../RUNBOOK.md) §4.7.
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
| User-facing instruction | `README.md` install step 3; `RUNBOOK.md` §4.7 |
| Open decision | `docs/PRD.md` §9 OD-1 |
