---
type: Design
title: "Design — MLX Studio interface"
status: "Built at tag v2.0.0 (2026-11-24); unreleased API Server additions noted in 7.4; other 2.0.x changes not re-reviewed"
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
| Toast | `#toast` | bottom centre, 3.2 s, `.bad` variant. Unreleased: a refused or failed Load request uses the panel below instead |
| Load-failure panel (unreleased) | `#loadProblemBox` | bottom right, red border, `role="alert"`; stays until **Dismiss** or the next load |

## 6. State vocabulary

This is the contract between backend and interface. Every surface derives from
`/api/state`.

### 6.1 Model

2.0.13 source (unreleased). Menu order: **API Server**, **Chat**, Models,
Download, Configuration, Load Test, Activity, Setup; the app opens on
**API Server**.

Chat and the sidebar follow the API server.

| Server | Sidebar pill | Chat status | Chat notice | Input |
|---|---|---|---|---|
| stopped | `No model served`, amber | `Server stopped` | **Start the API server first.** + **Go to API Server** | off |
| starting | `Starting…`, blue | `Starting…` | wait text | off |
| restarting | `Restarting…`, blue | `Restarting…` | wait text | off |
| load failed | `Model not loaded`, red | `Model not loaded` | + **Go to API Server** | off |
| ready | served names, green | `Ready` | none | on |

A served model's card carries a green `served` tag.

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

- Topbar: `Model` label, native select of the served models (max 380 px), status,
  **＋ New chat** on the right. Reasoning shows in a collapsed **Thinking** block.
  A reply with no answer after its thinking shows that text as the reply.
- Transcript: max 860 px centred column; user messages use `--panel-2` and an
  accent role label; assistant messages use `--panel`.
- Streaming: the live message body carries `.cursor`, a blinking block.
- Metrics: a `.msg-meta` line per completed turn — `N tokens · X tok/s`, plus `stopped` or `length limit reached`.
- Composer: auto-growing textarea, **Send** accent, **Stop** appears only while
  generating and Send hides.
- Empty state: mark tile, "Start a conversation", one sentence of instruction.

### 7.2 Models

- Header: title plus `N model(s)` summary on the right.
- Card: short name, full repo id, size, then **Reveal**, **Delete**.
- A served card carries a green `served` tag.
- Empty state points at Download.

### 7.3 Download

- Topbar: search input, **Search**, **Show all**.
- One card layout for recommended and search models; unknown fields are labelled,
  never filled in.
- Active Downloads starts collapsed; its header shows count and progress.
- The toggle's name is fixed (**Active downloads**); `aria-expanded` gives its state.
- A busy **Download** button uses `aria-disabled`, so it keeps keyboard focus.

### 7.4 API Server

- Topbar: title and state pill only.
- Endpoint panel: monospace URL, **Copy**, one-line hint, `curl` example.
- Output panel: live subprocess stdout, auto-scrolled only when already at bottom.
- Unreleased (2.0.12 source): one card, top to bottom.

  | Part | Design |
  |---|---|
  | **Models (max 2)** | A list: short name (full id in the tooltip), **about NK context** when known (K = tokens ÷ 1024), and **×** labelled **Remove <model id>**. Empty: **No model chosen.** |
  | **+ Add model** | Opens the searchable picker, which offers only models not on the list. Off with a reason: **Two models is the maximum. Remove one to add another.**, **No other installed model to add.** or **No models installed. Get one on the Download page.** |
  | Memory | **Memory: X / Y GB**: need out of allowed, or measured use out of the server's budget for the unchanged running server. Hidden while a pair is refused: the refusal gives its own figures |
  | Reason line | One line, amber: **Starting the server…**, the last start error, **Add a model to start the server.** / **Add a model, or press Stop.**, **No memory estimate: …**, the refusal, **Shorter context: about NK tokens per model.** (two), **Shorter context: about NK tokens.** or **This model may not fit: …** (one, never blocked) |
  | **Details** | Closed by default: the take-turns hint and safe-context line (two only), what **Memory** means, the estimate, the measured memory and the prompt length |
  | **Background** | Checkbox; changing it is a change to apply |
  | Buttons | **Start Server** (stopped) or **Apply changes** (running; off until the set, in any order, or **Background** differs), **Starting…** while a start is under way, **Stop** while running or starting. Beside a pending change: **Applying restarts the server: a reply in progress will be interrupted.** |

  - State: the list is the user's edits, else the running server's models, else
    the last set that ran (when the app has just opened, the last set whose start
    reported ready; models no longer installed completely are left out). Polls
    never overwrite edits. A new server process drops them, so the list shows what
    really runs, except edits made after its start was sent: those stay as a change
    to apply.
  - **Restart** in the stopped banner sends the models and **Background** mode that
    last ran (after a start from stopped that never ran, those that were sent), not
    an unapplied choice, which stays on the card. Models no longer installed
    completely are left out. When a new set and the previous one both fail, it may
    retry the failed new set. **Background** mode is not remembered after the app
    is closed (`server.last.json` holds model ids only).
  - The second click of a double click on a button or checkbox of this page is
    ignored. At 480 px or less a row's context sits under the name.
  - Keyboard and screen readers: adding returns focus to **+ Add model** (at two,
    to the new row's **×**); removing moves it to the remaining **×**, else to
    **+ Add model**; **Escape** closes the picker and returns to **+ Add model**; a
    focused button that turns off or hides passes focus to the next usable one.
    **Start Server** / **Apply changes** passes it to **+ Add model**, or to
    **Background** when that is off, never to **Stop**. **Restart** and
    **Restart now** pass it the same way when their banner goes. A further start,
    from any of these three buttons, is ignored until the status after the first
    one is on screen. A hidden status line says **Added …** / **Removed …**.
  - The estimate is asked again every 10 s while stopped or with a change waiting;
    an unchanged running server keeps its answer, and a refused estimate is not
    shown for it.
  - A start that replaces a server and fails brings the last working models back
    and shows the **Changes not applied** alert; if the restored server stops later,
    **API server stopped** takes over both texts (see API.md section 5).
  - Estimate words (GB to one decimal, K = context ÷ 1000, rounded down):

    | Case | Words |
    |---|---|
    | Shorter context | **Two models: about 10K tokens each (one model alone can use more). Uses about X of Y GB free.** plus **Close other apps for more.** only when free memory is the limit |
    | 32,768 | **Two models: 32K tokens each. Uses about X of Y GB free.** |
    | Different pair while a server runs | **Uses about X of Y GB available.** or **Needs about X GB, Y GB would be available.** (free plus the running server's memory) |
    | Same pair while a server runs | **Uses about X of Y GB available.** or **Needs about X GB, Y GB would be available.** (free plus the running server's memory) |
    | Free memory unreadable | **Uses about X GB.** then **Free memory could not be read; using the fixed 52% rule.** |
    | Refused, free memory | **Not enough free memory for two models: X GB free, Y GB needed for 8K each. Close other apps or start one model.** |
    | Refused, 52% rule or unreadable | **Not enough memory for two models: X GB allowed for the API server, Y GB needed for 8K each. Choose smaller models or start one model.** |
  - Under a running pair: **Keep prompts to T tokens or fewer. The prompt cache holds
    about 2T tokens in total; above that, older prompts are dropped and read
    again.** The cap is 2 × KV × T bytes, so by arithmetic a single conversation
    up to about 2 × T can still be cached, pushing out the other; the line is the
    safe limit, not the exact one (not tested).
  - The visible reason line is hidden from screen readers; a status line that is
    always in the page speaks it, only when it changes, and describes the main
    button.
  - A measured memory line: active, peak and budget with two models; active and
    peak only with one. The
    two-model line says **Measured by the API server, refreshed about every 10 s.**
  - One notice, inside an always-present status region. The first that applies, in
    this order: **One model at a time**; **Loaded without enough memory**; **Free
    memory is low**; **Loaded models changed** (a model was replaced); **Second
    model not preloaded**; **Loaded models changed** (unloaded to make room). The
    pill lists the loaded models, current one first.

    | Title | Text |
    |---|---|
    | **Loaded without enough memory** | Names the model, says it does not fit in the memory the API server may use now (with the GB figure and **with room for a T-token reply** when known), then **The other model was unloaded.** if an eviction of it is in the recent events. It ends **macOS may slow down or stop the API server. Free some memory or choose a smaller model.** |
    | **Free memory is low** | **Other apps now use memory the API server was counting on (X GB short). Replies may slow down. Close apps, or restart the server for a smaller context.** |

  - The **server unprotected**, **API server stopped** and **Changes not applied**
    alerts sit above the card.
  - A **Prompt limit** line names the longest prompt per served model.
  - A link, **Context, reply limits and memory: open Configuration**, stands
    where the Settings disclosure was (7.7). A max context above the memory
    ceiling shows a memory warning on the card.
  - Without a session token the app shows one toast, "This page has no session.
    Open MLX Studio from its app icon."

### 7.5 Activity

- Topbar: title, action filter (8 options), **Refresh**, **Clear**.
- Table: Time · Action · Model · Status · Detail; newest first.
- Status colouring: `success` green, `failed` red, `running` blue.
- Empty state explains what will appear.

### 7.6 Setup

- Application panel: version, latest release, channel, Python, hardware, last check,
  plus an **App update available** panel when the manifest is newer.
- Runtime packages panel: MLX, MLX-LM, huggingface-hub with version and pill;
  **Install / Update** installs `mlx`, `mlx-lm` and `huggingface-hub` 2.4.0 (newer
  versions are kept), streaming output into a console; the hub row has an update pill.
- Hint line states the conclusion in words: "Everything is installed and up to
  date." / "A newer MLX release is available on PyPI."

### 7.7 Configuration (2.0.13, unreleased)

- One panel: **Settings for** (the searchable model list: All models, then
  installed models), notes, a field
  grid (one column when narrow), **Save**, **Reset this entry**.
- Each field: label, number input, **Effective: …** line saying where the value
  comes from. Placeholder: `automatic` or `inherited: N`.
- Memory limit is read only under a model.
- **MLX buffer cache limit (MB)** is empty for MLX's default; 0 is a real value.
- **Performance mode** comes first: one list with one plain sentence. The three raw
  fields show only under Advanced; 4-bit shows a warning. Under a model, choices it
  cannot use are disabled and the reason is written beside them.
- Chat: the mode in force and a link to Configuration under the top bar (not a live
  region); a small stats line added under a reply only once it has ended.
- API Server card: when the one-model automatic context is below 4096, an amber
  line says every prompt would be refused and what to do.
- Amber lines: the lowered-context message, and a context above the largest size
  that passed a load test.
- **Apply changes** notice while the running server is behind the saved values.
- Invalid fields get a red border and their own message; one summary is announced
  and the first invalid field takes focus. Nothing is sent.
- Unsaved edits survive polling and page switches. Changing the model, or applying
  a load test size to that model, asks before dropping them.

### 7.8 Load Test (2.0.13, unreleased)

| State | Shown | Controls |
|---|---|---|
| No model | Link to Download | All off |
| API server running | The reason | **Stop API server**; Start off |
| Idle | Last result, earlier runs folded, **stale** tag | Start; Apply on passed rows of current runs |
| Running | Searchable model list (off), size i of n, step, read bar, memory now and limit | **Cancel test** only |
| Cancelling | **Stopping the test…** | Cancel marked unavailable, keeps focus |
| Memory stop or skip | **Stopped for memory safety** with the measured or projected figure and the limit | Start; Apply on earlier passed rows |
| Failed, cancelled, interrupted | Title and one reason | Start |

- Sizes are check boxes from the settings, the last used and typed ones; none is
  ticked on opening. A size over the model's limit is off and says why.
- Offered sizes double from 4096 to 1,048,576. A line under them says large sizes
  take long and get 3600 seconds above 32768.
- **Thinking** check box: on by default; off and disabled, with the reason, where the
  chat template has no `enable_thinking`. Its note says it uses the reply tokens. The
  run title says "thinking on" or "off"; memory figures of the other choice are not
  reused.
- Run values sit in a closed disclosure and are saved when a test starts.
- Only a new step or the end is announced. Counts and memory change silently.
- Table columns: Size, Status, Read tok/s, First token, Write tok/s, Peak memory,
  Settings used, and the Apply button. It scrolls sideways when narrow.

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
| No transcript persistence | Closing the app loses chat | Activity log records actions and outcomes, not prompt text |
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
