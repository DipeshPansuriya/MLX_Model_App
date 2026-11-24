---
type: Design
title: "Design — MLX Studio interface"
status: "Built at tag v2.0.0 (2026-11-24)"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.0"
related:
  - PRD.md
  - ARCHITECTURE.md
  - ../screenshots/
---

# Design — MLX Studio interface

## 1. What this document is for

It records the interface as shipped: the layout model, the design tokens, the
state vocabulary, and the rules that keep the six screens consistent. It exists so
a change can be judged against intent rather than taste, and so the visual language
survives a rewrite of the front-end.

The reference points are **LM Studio** and **Ollama**: dark, dense, keyboard-first,
with the model's state always visible and never more than one click from chat.

## 2. Principles

| # | Principle | Consequence in the UI |
|---|---|---|
| 1 | **State is always visible** | The sidebar pill shows model and server state on every screen |
| 2 | **One primary action per screen** | Exactly one accent-filled button; everything else is neutral |
| 3 | **Chat is the centre** | Chat is the first nav item and the default route |
| 4 | **Never lie about progress** | Three distinct states — not loaded, loading, loaded — never a spinner in place of truth |
| 5 | **Density over decoration** | 13–14 px body text, 28 px table rows, hairline borders, no shadows except on hover |
| 6 | **Destructive actions are quiet and red** | Delete is outlined red, right-aligned, and confirms |
| 7 | **The keyboard is enough** | Enter sends, Shift+Enter newlines, hash routing gives Back/Forward |

## 3. Layout model

```
┌────────────┬──────────────────────────────────────────────────────────┐
│  Sidebar   │  Topbar: title · context controls · primary action       │
│  232 px    ├──────────────────────────────────────────────────────────┤
│            │                                                          │
│  brand     │  Scroll region                                           │
│  ─────     │  (chat transcript | cards | table | panels)              │
│  nav ×6    │                                                          │
│  ─────     │                                                          │
│  hardware  ├──────────────────────────────────────────────────────────┤
│  status    │  Composer (chat only)                                    │
└────────────┴──────────────────────────────────────────────────────────┘
```

| Region | Fixed size | Behaviour |
|---|---|---|
| Sidebar | 232 px, collapses to 68 px below 900 px | Brand, nav, hardware, global status |
| Topbar | Content height (~63–65 px) | Per-screen controls; never scrolls |
| Scroll region | Flex | The only scrolling area per screen |
| Composer | ~80 px, grows to 190 px | Chat only; textarea auto-height |

The shell is `display: flex; height: 100vh; overflow: hidden`. Each view is
`display: none` until active, then `display: flex; flex-direction: column`. This
guarantees exactly one scroll container per screen and no page-level scroll —
verified at 1360×900 with zero horizontal overflow on all six views.

## 4. Design tokens

Defined once in `:root` (`ui/style.css:6`) and referenced everywhere.

### 4.1 Colour

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0f1115` | App background |
| `--bg-2` | `#14171d` | Sidebar, composer |
| `--panel` | `#171b22` | Cards, panels, inputs |
| `--panel-2` | `#1c2129` | Hover, active nav, user message |
| `--line` | `#262c36` | Hairline borders |
| `--line-2` | `#313846` | Control borders |
| `--text` | `#e6e9ef` | Primary text |
| `--text-2` | `#9aa4b2` | Secondary text |
| `--text-3` | `#6b7482` | Labels, metadata |
| `--accent` | `#4f8cff` | Primary action, links, running state |
| `--green` | `#37d399` | Loaded, success, up to date |
| `--amber` | `#f0a23c` | Not loaded, update available |
| `--red` | `#ff6b6b` | Failure, destructive |

Semantics are fixed: **green means resident or current, amber means attention
without failure, red means a failed operation.** Colour is never the only signal —
every pill carries a word.

### 4.2 Type

| Role | Family | Size | Weight |
|---|---|---|---|
| Body | `-apple-system`, SF Pro Text | 14 px | 400 |
| Chat body | same | 12 px | 400, 1.62 line-height |
| Title | same | 16 px | 650 |
| Section head | same | 12 px, uppercase, 0.08em | 750 |
| Label | same | 10–11 px, uppercase | 700 |
| Mono | SF Mono, Menlo | 11–12.5 px | 400 |
| Brand | same | 14.5 px | 650 |

### 4.3 Space and shape

| Token | Value |
|---|---|
| `--radius` | 12 px (cards, panels, composer) |
| `--radius-sm` | 8 px (buttons, inputs, selects) |
| `--shadow` | `0 8px 30px rgba(0,0,0,.35)`, hover only |
| Card padding | 15–18 px |
| Grid gap | 12 px |
| Card grid | `repeat(auto-fill, minmax(340px, 1fr))` |

## 5. Components

| Component | Class | States |
|---|---|---|
| Button | `.btn` | default, hover, active (1 px press), disabled (45% opacity) |
| Primary button | `.btn-primary` | accent fill; disabled desaturates to `#a7c8ea` |
| Danger button | `.btn-danger` | outlined red; hover tints background |
| Mini button | `.btn-mini` | 11.5 px, used inside cards |
| Select / input | `.select`, `.input` | focus ring `0 0 0 3px rgba(79,140,255,.16)` |
| Card | `.card` | hover lifts 1 px and adds shadow |
| Panel | `.panel` | static container with uppercase head |
| Pill | `.pill` | `.ok`, `.warn`, `.bad`, or neutral |
| Dot | `.dot` | `.dot-ok`, `.dot-warn`, `.dot-bad`, `.dot-live` (pulse) |
| Table | `.table` | hover row tint; status cells coloured |
| Console | `.console` | monospace on `#0f1115`, max-height 340 px |
| Empty state | `.empty` | mark tile, heading, one-line hint |
| Toast | `#toast` | bottom centre, 3.2 s, `.bad` variant |

## 6. State vocabulary

This is the contract between backend and interface. Every surface derives from
`/api/state`.

### 6.1 Model

| Backend | Sidebar pill | Chat header | Models card | Load button |
|---|---|---|---|---|
| `loaded.repo = null` | `● No model loaded`, grey | `● Not loaded`, amber | no tag | **Load** |
| `loaded.loading = repo` | `● Loading <name>…`, blue, pulsing | `● Loading <name>…` | — | disabled `Load` |
| `loaded.repo = repo` | `● <name>`, green | `● Loaded — <name>`, green | `loaded` tag, green | **Unload** |
| `loaded.error` set | `● No model loaded`, grey | `● Load failed`, red | — | **Load** |

### 6.2 Server

| Backend | Server pill | Nav dot | Buttons |
|---|---|---|---|
| `server.running = false` | `● Stopped`, amber | dim | **Start** |
| `server.running = true` | `● Running — <name>`, green | green | **Stop** |

### 6.3 Versions

| Condition | Pill |
|---|---|
| Installed, latest equal | `up to date`, green |
| Installed, latest newer | `update → x.y.z`, amber |
| Not importable | `missing`, red |
| No channel configured | `local build`, neutral |

## 7. Screen specifications

### 7.1 Chat

- Topbar: `Model` label, model select (max 380 px), **Load**/**Unload**, state text,
  **＋ New chat** on the right.
- Transcript: max 860 px centred column; user messages use `--panel-2` and an
  accent role label; assistant messages use `--panel`.
- Streaming: the live message body carries `.cursor`, a blinking block.
- Metrics: a `.msg-meta` line per completed turn — `N tokens · X tok/s · Y GB peak`.
- Composer: auto-growing textarea, **Send** accent, **Stop** appears only while
  generating and Send hides.
- Empty state: mark tile, "Start a conversation", one sentence of instruction.

### 7.2 Models

- Header: title plus `N model(s)` summary on the right.
- Card: short name, full repo id, size, then **Load/Unload**, **Reveal**, **Delete**.
- Loaded card carries a green `loaded` tag.
- Empty state points at Download.

### 7.3 Download

- Topbar: search input, **Search**, **Show all**.
- Result card: short name, repo id, **Download**.
- Downloading card replaces the action with a status tag: `running`, `success`, `failed`.

### 7.4 API Server

- Topbar: title, state pill, model select, **Start**/**Stop**.
- Endpoint panel: monospace URL, **Copy**, one-line hint, `curl` example.
- Output panel: live subprocess stdout, auto-scrolled only when already at bottom.

### 7.5 Activity

- Topbar: title, action filter (8 options), **Refresh**, **Clear**.
- Table: Time · Action · Model · Status · Detail; newest first.
- Status colouring: `success` green, `failed` red, `running` blue.
- Empty state explains what will appear.

### 7.6 Setup

- Application panel: version, latest release, channel, Python, hardware, last check,
  plus an **App update available** panel when the manifest is newer.
- Runtime packages panel: MLX, MLX-LM, huggingface-hub with version and pill;
  **Install / Update** runs pip and streams output into a console.
- Hint line states the conclusion in words: "Everything is installed and up to
  date." / "A newer MLX release is available on PyPI."

## 8. Interaction rules

| Rule | Rationale |
|---|---|
| Enter sends; Shift+Enter newlines | Chat convention; no accidental newline |
| Composer grows to 190 px then scrolls | Long prompts stay readable without eating the transcript |
| `location.hash` mirrors the active view | Back/Forward work; a view is linkable |
| Unknown hash falls back to `#chat` | No blank screen from a stale link |
| Poll every 2 s | Fast enough to feel live, cheap enough to ignore |
| Chat streams over its own connection | State polling never blocks tokens |
| Delete confirms; Clear confirms | Irreversible actions need a second act |
| Stop keeps the partial answer | Losing text on cancel is worse than a truncated answer |
| Toasts, not modals, for transient outcomes | Modals interrupt; toasts inform |
| Console auto-scroll only when pinned to bottom | Reading history must not be yanked away |

## 9. Accessibility

| Concern | State |
|---|---|
| Contrast | Body text `#e6e9ef` on `#0f1115` ≈ 15:1; secondary `#9aa4b2` ≈ 7:1; both pass AA |
| Colour independence | Every status pill pairs colour with a word |
| Keyboard | All controls are native `button`/`select`/`input`; focus rings visible |
| Focus order | DOM order matches visual order in every view |
| Motion | Only the cursor blink and the live dot pulse; both are opacity-only |
| Zoom | Layout is flex-based; the 900 px breakpoint collapses the sidebar to icons |
| Screen readers | Semantic headings and table headers; live regions **not** yet used for streaming — see §11 |

## 10. Responsive behaviour

| Width | Behaviour |
|---|---|
| ≥ 900 px | Full sidebar with labels and counts |
| < 900 px | Sidebar collapses to 68 px; labels, counts and footer hidden; nav icons centred |
| Any width | Card grid reflows by `minmax(340px, 1fr)` |

## 11. Known gaps

| Gap | Impact | Note |
|---|---|---|
| No `aria-live` on the transcript | Screen readers do not announce streaming tokens | Would need a polite live region on the assistant body |
| No reduced-motion query | Pulse and blink always animate | `prefers-reduced-motion` not yet honoured |
| No light theme | Dark only | Tokens are centralised, so a theme is a token swap |
| No transcript persistence | Closing the app loses chat | Activity log records prompts and outcomes only |
| Fixed sampling | No temperature/top-p controls | `temp=0.7`, `top_p=0.95`; PRD OD-4 |

## 12. Screenshots

| View | File |
|---|---|
| Chat, empty | [01-chat-empty.png](../screenshots/01-chat-empty.png) |
| Chat, loaded with reply | [02-chat-loaded.png](../screenshots/02-chat-loaded.png) |
| Models | [03-models.png](../screenshots/03-models.png) |
| Download | [04-download.png](../screenshots/04-download.png) |
| API Server | [05-server.png](../screenshots/05-server.png) |
| Activity | [06-activity.png](../screenshots/06-activity.png) |
| Setup | [07-setup.png](../screenshots/07-setup.png) |
| Setup, app update available | [08-app-update.png](../screenshots/08-app-update.png) |

## 13. Changing this file

A visual change updates §4 (if a token moved), §5 (if a component changed), §6 (if
a state was added) and §7 (if a screen changed), and re-captures the affected
screenshot in the same commit.
