# Changelog

All notable changes to MLX Studio are documented here.
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.13] — unreleased

Checked with automated tests and the screen served against a fake API server. No
model was loaded. The direct `mlx_lm` server below has never been started for real.

### Removed (protections: read before upgrading)

MLX is now `mlx_lm`'s own server. MLX Studio's request guard (`mlx_guard.py`) and the
80% memory rule are gone, with no replacement. Entries further down that describe
them for this version are superseded by this list
([full table](README.md#protections-removed-in-2013)).

- No memory limit anywhere: a large model or prompt can freeze or crash the Mac.
  The OptiQ memory stop and the Load Test size gate are removed too.
- No MLX prompt limit, long prompt refusal or reply token cap.
- One model per server; no two-model mode.
- No `Host`, JSON-only or `Origin` checks and no request size limits on port 8080;
  only `--allowed-origins`.
- A request can make the server load another cached model, a local folder, an
  adapter or a draft model.
- Settings removed: memory limit, MLX buffer cache limit, two-model context cap.
  Max context is used by OptiQ only.
- No memory estimates, loaded-model readout or **Apply this size to config**
  (`/api/server/estimate` and `/api/loadtest/apply` are removed).

Kept: `127.0.0.1` only, OptiQ key and port checks, the app page's request checks,
clean offline server environment, process cleanup, key hiding. The Load Test still
stops on critical memory pressure, swap growth over 0.5 GB, unreadable memory or a
sampling gap over 1 second. Clients should send the model name `default_model`.

### Added

- **Engines** ([docs/ENGINES.md](docs/ENGINES.md)): the API Server and Load Test can use
  OptiQ 0.8.6 beside MLX (default). Setup installs, updates and removes it from a hashed
  package lock. It has a key, an Anthropic route and fewer protections than MLX; the
  card lists them. Splash and Rapid-MLX are shown as not available yet. A live check over
  the app's HTTP routes passed with a real model; the Setup install button, clicks in the
  screens, a Claude Code client and the load test are untried.
- Under OptiQ: the engine folder is checked at each start, keys are hidden in the
  server log, and an installed engine is never installed over. These were checked
  with fakes only.

- Load Test: sizes up to 1,048,576 (up to 12 in one test), with 3600 seconds for a
  size above 32768; at the model's own limit the prompts stop short so the replies
  fit. A **Thinking** switch where the chat template has `enable_thinking`; results
  say which it was. Checked with fakes only.

### Fixed

- The Anthropic switch is hidden under MLX; client settings and **Copy key** follow
  the running engine; Configuration says what OptiQ ignores or treats differently.
- **Remove** is off while that engine serves; the Load Test engine is locked during a
  run; OptiQ messages name the next step; Chat says OptiQ drops older text.

### Changed

- The MLX server and load test processes no longer inherit the app's whole environment:
  only paths, locale, Python, Hugging Face cache and offline variables and
  `MLX_STUDIO_*`. Keys and tokens are not passed on.
- **API Server is first in the menu, Chat second**, and the app opens on the API
  Server page.
- **Chat uses the running API server.** No second copy of a model is loaded, and
  the **Load** and **Unload** buttons are gone from Chat, Models and Download. The
  Model list holds only the served models; one model is selected already. While the
  server is stopped Chat offers **Go to API Server**; while it starts, typing is
  off. A busy server shows a waiting line, and **Stop** closes the request.
- Reasoning (`reasoning` field or `<think>` text) shows in a collapsed
  **Thinking** block. A reply with no end of thinking is shown in full; thinking
  alone is not kept in the chat history.
- Each reply reports `tokens · tok/s`; peak memory is no longer shown.
- The memory estimate no longer counts a Chat model.
- `/api/load` and `/api/unload` are removed; `/api/chat` needs `repo`.

### Added

- **Load Test page.** Tick or type context sizes, watch progress and memory,
  cancel, compare results with earlier runs and apply a passed size to the model's
  config. `mlx_loadtest.py` tests the sizes with its own guarded server while the
  API server is stopped; routes `/api/config` and `/api/loadtest`. It stops at 77% of memory and sends nothing
  projected above 75%; sampling cannot guarantee 80%. Not run with a real model.
  [Routes and limits](docs/API.md#6c-configuration-and-load-test).
- **Configuration page**, for all models or one: it replaces the Settings box on
  the API Server card, which now links to it. Menu: API Server, Chat, Models,
  Download, Configuration, Load Test, Activity, Setup.
- **User settings**, kept in `~/.mlx_studio/settings.json`: max
  context tokens, two-model context cap, memory limit percent (30 to 80), server
  and Chat reply caps, prompt cache size and bytes, MLX buffer cache limit. Empty
  means automatic. A max context above the memory ceiling (80% of RAM, or the
  memory percent, by the app's formula) is lowered to it, and the app says which
  figure applies. Each model's own max context also applies after the server
  switches models. Chat temperature and top-p can be set. Ports, hosts, the token and security
  checks cannot be set. The file and `/api/server/settings` also take overrides per
  model (all keys but the memory percent); two models use the stricter value. [Keys and ranges](docs/API.md#6b-user-settings).
- **The API server refuses a too long prompt at once** (`400`,
  `context_length_exceeded`) instead of reading it for minutes. The limit is shown
  on the API Server card and is never above the model's own. Two models: the
  context the server was started with. One model: what fits in 80% of RAM after
  taking off the memory other apps hold when the server starts. If that cannot be
  read: what fits in 52% of RAM, at least 32,768 (or the model's smaller limit),
  but never more than fits in 80%.
- **The one-model memory figure is higher and the context lower.** Reply room is
  6.4 times the cache bytes per token (was 3.1), fitted to measurements of one
  model on one Mac (Qwen2.5-7B 4-bit, Apple M6, 32 GB); other models and Macs are
  calculated. On that Mac the automatic limit is about 21,000 tokens with other
  apps at 41% and 12,600 at 50% (calculated). Below 4096 every prompt is refused
  (code `insufficient_memory`) with the memory in use and a message to close other
  apps, then press **Stop**, then **Start Server**. A set Max context follows the
  same rule, other apps included; there is no override.
  Not run with a real model, and no guarantee that memory stays under 80%.
- **Performance mode** on Configuration, for all models or one: **Standard
  (recommended)** changes nothing; **Save memory** passes `--kv-bits 8
  --prefill-step-size 256`, which uses less memory but reads new prompts about half
  as fast (measured on one model and one Mac); **Advanced** sets `--kv-bits` (8 or
  4, with a warning for 4), `--kv-group-size` (32, 64, 128) and
  `--prefill-step-size` (64 to 2048). Used by the API server and the load test.
  A model that cannot compress its cache is refused the choice, with the reason,
  and runs in Standard. The automatic prompt limit is not raised.
- **Chat reply stats**: time to the first answer, writing speed, prompt size and
  mode under each reply (unknown figures are left out), and the mode in force at the
  top of the page.
- **MLX buffer cache limit** (`mlx_cache_limit_mb`, Configuration page, global and
  per model; also used by the load test): `mx.set_cache_limit` in decimal MB. Empty
  keeps MLX's default. Its effect on speed and memory is not measured.

### Fixed

Pre-release review, security and browser findings:

- A reply is capped so that prompt plus reply stays within the prompt limit (at
  least one token).
- The prompt cache holds 2 conversations by default (`--prompt-cache-size 2`) and
  has a byte cap with one model too: 2 times cache bytes per token times the limit.
  `prompt_cache_bytes` only lowers it.
- A model that accepts no prompt is shown as such on the API Server card. The card
  sizes one model at what it will serve, not always 32,768 tokens.
- Load test: a size above what the server accepts is **skipped**, and requests go
  only to a port held by the test's own server. A cancel marks the remaining sizes
  cancelled.
- Chat: an unreadable progress line no longer ends the reply; no prompt is sent to
  an adopted server that is no longer ours; a double click on **Send** does not
  stop the reply; a stopped reply is announced as stopped; focus moves off a
  **Stop** button that goes away; a failed model load is said on both pages.
- Configuration shows the applied max context and the one-model memory rule.
  "Chat only" hints are read with their fields.

- Reading the remembered model set no longer leaks a file descriptor when its path
  is a folder.
- In a narrow browser window the Load Test page no longer scrolls sideways, and
  **New chat** stays on screen in Chat.

## [2.0.12] — 2026-11-27

Not released: pre-release build; the released 2.0.9 app has none of these changes.
It holds the 2.0.10 and 2.0.11 changes and those below. Checked with automated
tests and the screen served with made-up data. No model was loaded and no API
server was started. **The real-model and two-model memory tests have not been run.**

### Changed

- **Simpler API Server screen.** One card lists up to two models, each with its
  context figure when known and a remove button; **+ Add model** opens the
  searchable list. The **Also keep loaded** panel and the top-bar picker are gone.
- One **Memory: X / Y GB** line; the longer explanations moved under **Details**.
  A refusal or a shorter context is still shown on the card.
- **Start Server** is off, with the reason, until a model is chosen. On a running
  server the button is **Apply changes**, enabled only when the models or
  **Background** differ, with a note that a reply in progress will be interrupted.
- When the app opens with the server stopped, the card already holds the last
  models whose start reported ready (kept in `~/.mlx_studio/server.last.json`),
  so **Start Server** is one click; a model that is no longer installed completely
  is left out. With nothing remembered the card starts empty; before, the first
  installed model was preselected. Nothing is started by this.

### Fixed

- **Restart** leaves out remembered models that are no longer installed completely,
  and a start naming such a model is refused. A damaged or planted
  `server.last.json` (or its old temporary name) is ignored and never followed.
- **A failed change brings the last working models back.** If the new server is
  refused, cannot start, exits or cannot load its first model before it is ready,
  the last models that worked are started again once and **Changes not applied**
  says why. Applying again before the new server is ready keeps them as the
  fallback; **Stop** forgets them. This is one try: if it fails, or the restored
  server stops later, **API server stopped** gives the reasons and the server stays
  stopped.
- A start with one model that is not installed completely no longer stops the
  running server first.
- A first start that cannot run now shows **API server stopped** with the reason.
- The second click of a double click on the API Server page does nothing, so it
  can no longer start or restart the server, change **Background** or remove a
  second model. A second press on **Restart** or **Restart now** while the first
  is on its way no longer starts twice.
- Models and **Background** changed while a start is under way stay on the card as
  a change to apply.
- **Restart** after a stop brings back the models and mode that last ran, not an
  unapplied choice, which stays on the card.
- Keyboard focus is no longer lost when **Stop** or a **Restart** button goes away.
- In a browser window 480 px wide or less, a model's context moves under its name.

## [2.0.11] — 2026-11-27

Not released: pre-release build; the released 2.0.9 app has none of these changes.
It holds the 2.0.10 changes and the fix below. Checked with automated tests; no
model was loaded. **The real two-model memory test has not been run.**

### Fixed

- **A model in use still counts while it is downloaded again.** In 2.0.10, a model
  that turned incomplete (a partial file, or an unfinished download) counted as
  0 GB with no configuration. A Chat model in that state was not taken off the
  two-model server's budget, so the memory check could accept a pair or a Chat load
  that does not fit. Its cached weights and configuration are now read. While a
  newer revision is still arriving, the complete earlier one is measured; if no
  revision is complete, the one holding the most model weights is.

## [2.0.10] — 2026-11-27

Not released: pre-release build; the released 2.0.9 app has none of these changes.
Checked with automated tests, temporary instances and offscreen web views. The real
app window was not launched. One earlier real-model run of two models led to the
memory limits below; **the re-test of those limits has not run yet.**

### Added

- **API Server can keep two models loaded.** A second picker, **Also keep loaded
  (optional)**, starts the server with two installed models on the same address.
  Models take turns: a request for one waits until the other's replies finish,
  then the server switches. A third downloaded model named by a client evicts
  the least recently used one. If memory is still short, the prompt cache is
  trimmed when that is enough; otherwise the other model is evicted too.
- A memory estimate and a "safe context" estimate for the pair. **Context per
  model** is at most 32,768 tokens, rounded down to a multiple of 1,024. It
  follows the smaller of 0.52 × RAM (less the weights of the app's Chat model)
  and live free memory from `/usr/bin/vm_stat`, less 2 GB left for macOS and
  other apps. The sum keeps a 1.4 GB margin and 3.1 times one reply's cache.
- Below 32,768 the pair starts with a shorter context and the view says so. Below
  8,192 tokens each, two models are refused (`409`) with the figures. If
  `vm_stat` cannot be read, only the 52% rule is used and the view says so.
- Free memory is checked again at each load of a model that is not in memory and
  when a Chat model is loaded. A **Free memory is low** notice appears when other
  apps take memory the server was counting on, only after preload has ended.
  Nothing is unloaded just for that.
- The API Server view shows measured memory (with two models active, peak and
  budget; with one model active and peak only), a line saying **keep prompts to
  T tokens or fewer. The prompt cache holds about 2T tokens in total; above
  that, older prompts are dropped and read again**, and a notice when a model is
  replaced, unloaded to make room, not preloaded, **loaded without enough
  memory**, free memory is low, or only one model can be kept.
- While two models are served, a Chat-tab load that does not fit beside them, by
  the 52% rule or by live free memory, is refused (`409`) with the figures. A
  model that does not fit the server budget on its own is still loaded alone, with
  a **Loaded without enough memory** warning.
- In two-model mode the prompt cache is capped at 2 × cache bytes per token ×
  context, about one long conversation per model, including requests run one at a
  time. A one-model server gets no byte cap, as in 2.0.9.
- API route `POST /api/server/estimate`, the `also` field on
  `/api/server/start`, and the state files
  `~/.mlx_studio/server.resident.json` and `~/.mlx_studio/server.budget`.

### Security

- Every reply from port 8765 carries a full `Content-Security-Policy`: scripts
  load only from the app's own files, with no inline script or `eval`.
- The session token is no longer written into the page. The app opens its window
  with the token in the URL fragment, which is not sent to the server; the page
  keeps it in `sessionStorage` and removes it from the address bar. `GET /` no
  longer reveals it.
- Every `Origin` header must be the app's own, not only the first.
- A POST to port 8765 needs exactly one `Content-Type` header.
- `PYWEBVIEW_LOG` is removed before the window library loads, so its debug
  logging cannot be switched on.
- Browser mode: if `osascript` fails, the address is opened inside the app's own
  process. If that fails too, nothing is opened and the app says to quit and open
  it again from its icon. The token is not put on a command line.
- `/api/load` and a one-model memory estimate accept only a completely installed
  model.
- The server's name list and memory budget files are not opened through a link.

### Changed

- `POST /api/load` answers `400` for a name that is not installed completely.
- A refused Load request on the Chat or Models view stays in a panel at the bottom
  right until **Dismiss** or the next load, instead of a short message.
- API Server view: the server warnings sit above the second picker, and the
  estimate and model warnings are marked up for screen readers (VoiceOver not
  checked).
- A page opened without the token (a typed or bookmarked address) shows one
  message, "This page has no session. Open MLX Studio from its app icon.", and
  sends no requests. The refused-token message is now "invalid session token; open
  MLX Studio again from its icon".
- In browser mode (no native window), a second launch of the app opens a new
  tab, at most once every 3 seconds. Not checked live: if macOS only brings the
  running app forward on an icon click, no tab opens.
- A crash notice names both models, and says "possibly out of memory" when the
  server process was killed.

### Fixed

- In the app bundle, the app now sets its own icon and name at start, so the Dock
  and confirmation boxes should no longer show Python's. Tested offscreen only:
  the real Dock, menu bar and macOS permission prompts are unverified.
- An unwritable `launch.log` no longer ends launch logging: it is moved to
  `launch.log.1` and a new file is opened. Launches started together take turns at
  opening and rotating the log.
- The build stops when `codesign` fails, and when `app.py` does not hold exactly
  one `APP_VERSION = "X.Y.Z"` line.

### CI and tests

- The macOS job installs the pinned `mlx`, `mlx-lm` and `huggingface-hub`, and
  fails if a test in the guard or packaging files is skipped or left out. These
  steps have not run on GitHub yet.
- The packaging tests run the build and installer scripts instead of matching
  their text. New test files cover the changes above.

### Known limits

- **The two-model memory limits have not been re-tested on real models.** Memory
  figures, safe context, eviction, cache trim, switch time and swap use are
  calculated or untested. One earlier real run, before these limits, drove system
  swap from 0 to 4.60 GB on a busy Mac. The 2 GB reserve, the 3.1 reply factor
  (one data point) and the `vm_stat` free figure are not proven.
- The budget never rises above the figure set at start: restart the server to get
  a larger context. A server started while a Chat model is loaded keeps the lower
  budget until it is restarted, and a background server left running after the app
  quits keeps a lowered budget until the app opens again.
- The Chat-tab check counts the new model's weights, not its reply cache, and not
  the memory of the Chat model it replaces, so a swap may be refused until
  **Unload**.
- At each load the server counts its 0.4 GB process overhead twice, so it trims
  or evicts a little early.
- If the check after stopping the old server refuses the start, the old server
  stays stopped. Right after a killed server, free memory may be read before
  macOS has returned it.
- A model whose context limit is below 8,192 can make the "needed" figure too
  small. Screen readers may announce notices again on later updates (not checked).
- The prompt hint says **keep prompts to T tokens or fewer. The prompt cache
  holds about 2T tokens in total; above that, older prompts are dropped and read
  again**. By the arithmetic one conversation up to about twice the context can
  still be cached, pushing out the other (not tested).
- A switch between two loaded models does no memory check. A model that does not fit
  the budget is still loaded on its own, and any local program using port 8080 can
  make the server switch models. A killed server loses both models.
- Browser mode only: the browser may keep the first address, with the token, in
  its history, although the page removes it from the address bar (not checked in
  a live browser). The token stops working when the app quits.
- `style-src-attr 'unsafe-inline'` stays in the policy for inline styles such as
  the download progress bar. Scripts are not affected.
- UNVERIFIED, not tested: the app process itself does not set `HF_HUB_OFFLINE`, so
  loading an installed model on the Chat tab may contact the Hugging Face Hub, and
  `mlx_lm` may prefer a local folder with the same relative name in the app's
  working folder.

## [2.0.9] — 2026-11-26

Security, robustness and build fixes, plus model search and copy. Checked by
automated tests and temporary servers; no older macOS was tested.

**After updating:** if the app shows *server unprotected*, open API Server and
press **Restart now**. A background server started by an earlier version keeps
running without the new request checks until it is restarted.

### Security

- **App API (port 8765) refuses other origins.** Every request needs a loopback
  `Host` for that port and, when sent, a matching `Origin`. Every POST needs
  `Content-Type: application/json`. Every `/api/*` request, GET and POST, needs a
  per-launch `X-MLX-Token` (`403` without it), except `GET /api/ping` and
  `POST /api/focus`. Exactly one `Host` header is accepted, every reply carries
  `X-Content-Type-Options: nosniff`, and framing the UI is denied. The token is
  not user authentication: a local program can fetch `/` and read it.
- **API server (port 8080) now starts through a request guard**
  (`mlx_guard.py`). It refuses requests from web pages and unknown host names,
  accepts only JSON POSTs, serves only models already in the cache, never
  downloads a model on request, and refuses `draft_model` and `adapters`.
- Images sent to port 8080 must be `data:` URLs. `http(s)` and other URLs get
  `400`, so a request cannot make the server fetch them. The guard does not start
  if the `mlx_lm` image helpers it relies on are missing or moved.
- An adopted background server that runs without the guard is flagged
  **server unprotected**, with a warning in the logs and a **Restart now**
  button. The app does not restart it by itself.
- The API server's allowed origins are the app's two origins instead of `*`.
- A saved server process id is only adopted or stopped when its command line and
  start time still match, so an unrelated process is not killed.
- The one-line installer verifies the DMG against `release/SHA256SUMS`, refuses
  to install without a matching entry, and keeps the old app until the new copy
  passes the signature check.
- The secret scanner prints only the rule and `path:line`, and the pre-push hook
  scans every outgoing file version.
- The secret scanner's placeholder allow-list judges only the secret value, never
  a key name, user name or the rest of the line, and every match on a line is
  checked. `$VAR` or `${VAR}` excuses only when it is the whole value. A
  blocking finding outranks an earlier warning on the same line, and non-UTF-8
  files are scanned. Use `secret-scan: allow` for other cases.
- The security audit fails when the secret scan cannot run, tests only the
  "others write" bit on tracked files, and compares every bundled file with the
  source.
- The API server guard records the name of each accepted model, so the app does
  not delete a model while it is served.
- `set_approval_gate.sh` passes the GitHub token to `curl` on standard input.
- The update panel only opens an `https` link and shows the published SHA-256.
- The native launcher sizes `launch.log` from the open file before rotating it,
  leaves a symlinked log alone and keeps the rotated log owner only.

### Added

- **Copy model name** button on model cards (copies the full repo id, the value
  API clients send as `model`).
- **Search field** for installed models on Chat and API Server: Up and Down to
  move, Enter to choose, Escape to close. The clear button empties the search and
  keeps the choice, which is shown under the field.
- Download **Cancel** and **Resume download**; progress is counted in bytes and
  names the current file.
- **Reveal** opens the model folder in Finder.
- API Server view warns when the chosen model is marked as unable to call tools.
- API routes `/api/download/cancel` and `/api/reveal`.
- Model cards show catalogue details (parameters, category, use) when known.

### Changed

- The API server allows **8192 reply tokens** by default (mlx_lm's own default
  is 512).
- Naming a model on port 8080 that is not downloaded now fails with `400`.
  Before, it was downloaded.
- Loading a model unloads the current one first, also when the new load fails.
- Load, Unload and a second chat are refused (`409`) while a reply is running.
- Delete is refused while a model is loaded, loading, served, starting or
  downloading.
- Incomplete downloads show as **incomplete** and cannot be picked for chat or
  the API server until finished.
- The model cache follows `HF_HOME` / `HF_HUB_CACHE`.
- Setup installs the pinned `mlx`, `mlx-lm` and `huggingface-hub` 2.4.0 versions
  (a newer installed version is kept), shows an update pill for the hub library,
  and explains the refusal from a system-managed Python.
- Hugging Face search results use the same cards as **Best for your Mac**, with
  fit, size and Load for installed models. Sizes marked (estimate) are worked out
  from the model's parameter count; missing details show as unknown.
- Active Downloads starts collapsed and shows count and progress in its header.
- Bad API requests answer `400`, `411`, `413` or `415` and close the connection.
- A window left open from an earlier launch must be reloaded.
- The build compiles `packaging/launcher.c` for macOS 12.0 and fails if the
  binary says otherwise. The launcher picks a Python 3.10+ (preferring one with
  `mlx_lm`) and writes `launch.log`. `packaging/launcher.py` is removed.
- `release_manifest.py` generates `release/latest.json` and `release/SHA256SUMS`
  from the built files and verifies them; the `--dmg`, `--url` and `--out`
  options are gone.
- `release_manifest.py --check` fails unless `release/SHA256SUMS` holds exactly
  one valid line for the manifest version's dmg. One exception: the pinned 2.0.7
  manifest, which was never published, passes while `APP_VERSION` is newer.
- New `--backfill` adds a published release to that file and needs
  `--dmg-sha256` (the digest GitHub shows). Changing a recorded checksum needs
  `--replace`.
- `set_approval_gate.sh` lists `Release consistency` as a required check (not
  yet applied to GitHub).
- The update panel button reads **Download installer**.
- CI adds a macOS build job and a release consistency job.

### Fixed

- The 2.0.8 local build's launcher required macOS 27.2.
- Concurrent requests could start two downloads, two loads or two API servers.
- A failed delete reported success.
- A partial or failed download was listed as installed.
- A failed chat reply stayed in the history and could break later turns.
- Model cards could lose their real disk size and show empty details.
- A finished download now shows **Installed** on its catalogue card.
- Tool calls no longer fail when the model sends `null` for an optional
  argument. The API server leaves such an argument out: only a top-level argument
  that the request's tool schema declares, does not require and gives one
  non-null type. A call is never left without arguments.
- A catalogue card follows the live model list, so it no longer keeps a stale
  **Installed** mark.
- A model card button keeps keyboard focus while its download starts, also when
  **Download** becomes **Load**.
- The downloads toggle has the fixed name **Active downloads** and announces
  open or closed.
- The **Background** box shows the running server's mode, and a later click is no
  longer undone by the refresh.
- The page no longer requests a missing favicon.
- A download is judged complete by the files `mlx_lm` reads, not by extras such
  as the README.
- A reply can no longer appear in a chat other than the one it started in; New
  chat during a reply also stops it.
- Stop now cancels an API server start that has not launched yet.
- Two quick install requests could both start.
- The 2-second refresh rebuilt cards and dropdowns, losing focus and selection.
- `server.log` is rotated before a background start; log rotation is locked.
- Accessibility: download progress bars expose their values, keyboard focus is
  kept on model cards, names for controls, status announcements, current-view marker,
  and higher text and button contrast. Not tested with VoiceOver; contrast was
  not measured in a running window.

### Known limits

- Other programs on the same Mac can still use port 8080 and switch between
  downloaded models.
- The session token on port 8765 does not stop other local programs or macOS
  users, because the open page `/` contains it.
- Cancelling in the middle of a Xet-backed file was not tested.
- The one-line installer needs a line for the version in `release/SHA256SUMS` on
  `main`. 2.0.8 is not backfilled: the file starts with the built and verified
  2.0.9, and 2.0.8 or older is refused ([DEPLOYMENT.md](docs/DEPLOYMENT.md)
  section 5).
- Known issue (low): the page's Content-Security-Policy only sets
  `frame-ancestors`. It has no `default-src` or `script-src`, so it would not stop
  an inline script if some text were left unescaped. The security check found no
  such place in the new cards, search fields or download cards.
- A `null` argument is still passed on when it is the call's only content (for
  example `{"cd": null}`), when the schema does not clearly refuse it, or when the
  model writes the text `"null"`. A client may then reject the call.
- Small models can still get multi-step tool tasks wrong. With
  `Qwen2.5-7B-Instruct-1M-4bit` and the test prompts, a read, sum and write task
  failed 5 of 5 runs through the model's own mistakes, also with a simulated
  `null` fix. It was not re-run with the final fix.

### Checksums

```
pkg  sha256  8ace82169bf180402ecbecb0b97e1f8cae887078458cce2332dd3434b571c66b
pkg  size    984877 bytes
dmg  sha256  fd61995979d8c874b2f211d7c528a77ed033503c53a9703f469c42030c9a1ecf
dmg  size    1018132 bytes
```

---

## [2.0.8] — 2026-11-25

Published on GitHub as release `v2.0.8` with a dmg and a pkg (checked on
2026-11-26).

- The tag `v2.0.8` holds the 2.0.7 source (`APP_VERSION` is `2.0.7`).
- Which source built the published 2.0.8 files is UNVERIFIED.
- A local 2.0.8 build was never committed to this repository; its changes
  (download-state locking, the collapsible downloads section, model card details
  and the download percentage label) are part of 2.0.9. No local build file that
  was compared equals the published 2.0.8 files.

---

## [2.0.7] — 2026-11-25, no GitHub release

Download and model-path fixes. There is no `v2.0.7` tag or release on GitHub
(checked on 2026-11-26). The `v2.0.8` tag holds this source.

### Fixed

- **Active Downloads were off-screen.** The section sat below the 25-card
  catalogue, at 1772px inside a 750px scroll container, so a started download
  looked like it did nothing. It now sits above the catalogue at 174px.
- **No model path in the UI.** Every card in the Models view and every completed
  download card now shows the full on-disk path in a selectable `.card-path`
  block.
- **Downloads never appeared in the Models list.** `worker_download` wrote to
  `~/.cache/huggingface` while `installed_models()` scans
  `~/.cache/huggingface/hub`, so a downloaded model was invisible. It now passes
  `CACHE_DIR`.

### Added

- **28 download regression tests** in `tests/test_downloads.py` covering cache
  location, progress reporting, failure handling, the duplicate guard, the state
  payload, and the UI contract (section order, progress bar, path block). The
  suite is now 189 tests.

## [2.0.6] — 2026-11-25

Download progress indicator — adds a progress bar and per-file percentage
to the Download view so users can see which model files are downloading.

### Fixed

- **Download progress bar.** `worker_download` now reports per-file progress
  as it streams each file from Hugging Face, and the Download view renders a
  progress bar with percentage instead of only showing a static status tag.

## [2.0.5] — 2026-11-25

Single-instance native launcher — fixes the multi-window bug.

### Fixed

- **Duplicate windows on relaunch.** Every double-click of a running app
  started a second process that died on `OSError: [Errno 48] Address already
  in use`, leaving the user with nothing. A second launch now raises the
  existing window instead. Verified: repeated opens leave exactly one
  `app.py` process and one onscreen window.

- **Duplicate Dock tiles.** The `.app` bundle's `CFBundleExecutable` was a
  bash script that exec'd `python3`, so LaunchServices registered TWO app
  identities ("MLX Studio" + "Python") and produced two Dock tiles. The
  executable is now a native Mach-O binary (`packaging/launcher.c` compiled by
  `packaging/build_app.sh`), so LaunchServices registers ONE identity and one
  Dock tile.

### Changed

- **`packaging/launcher.py` → `packaging/launcher.c`.** The launcher is now a
  native Mach-O executable built by `clang` in `packaging/build_app.sh`. It
  finds Python 3.10+, checks `/api/ping` for an existing instance, and if one
  answers, posts `/api/focus` to raise its window before exiting. If that
  fails (older instance without `/api/focus`), it falls back to an AppleScript
  frontmost call.

## [2.0.4] — 2026-11-25

Test suite, `.pkg` installer and the single-instance fallback. From this release
the `.pkg` is the primary artifact; the `.dmg` stays for people who want it.

### Added

- **Test suite** — 161 pytest tests across five modules: pure helpers, logging
  and rotation, every HTTP endpoint, API server lifecycle, and packaging. Runs in
  about 32 s with no MLX installed because `app.py` imports nothing heavy at
  module scope. `pytest.ini` registers the `integration` marker for tests that
  shell out to macOS tooling.
- **`Tests` CI job** — `.github/workflows/quality.yml` runs the suite on
  `ubuntu-latest` for every PR.
- **`.pkg` installer** — `packaging/build_app.sh` now emits
  `MLX.Studio-<version>.pkg` alongside the `.app` and `.dmg`. `pkgbuild` installs
  to `/Applications` with one double-click, and the installed bundle carries no
  `com.apple.quarantine` attribute, so the "Not Opened" dialog does not appear.
- **AppleScript focus fallback** — `launcher.py` raises the running window via
  `osascript` when `/api/focus` is unavailable, which is what happens when a
  2.0.4 launcher meets a 2.0.2 or 2.0.3 instance.

### Fixed

- **Silent second launch.** A 2.0.3 launcher meeting a 2.0.2 instance got a 404
  from `/api/focus`, logged `focus requested (ok=False)` and exited 0 with
  nothing on screen. The fallback now runs, and the outcome is recorded in
  `launch.log` either way.
- **`POST /api/delete` with no repo** returned `404 not installed`; it now
  returns `400 repo required`, matching every other endpoint.

## [2.0.3] — 2026-11-25

Install documentation fix plus a single-instance fix. `2.0.2` told users to clear
quarantine before installing, but not how to actually run the DMG afterwards, and
every double-click of a running app started a second process that died on a busy
port and left the user with nothing.

### Fixed

- **Only one instance runs.** A second launch now raises the existing window
  instead of spawning a rival process. `POST /api/focus` restores a minimised
  window and brings the app forward; `launcher.py` checks `/api/ping` before
  starting anything, and `main()` re-checks after a failed bind to cover the race.
  Previously the second process died with `OSError: [Errno 48] Address already in
  use` and the window never appeared.

### Changed

- **README install section split into two numbered routes.** Option A is the
  one-line installer; Option B is the DMG, with Step 1 clear the flag, Step 2
  mount, Step 3 drag to Applications, Step 4 eject, Step 5 launch, Step 6 verify
  with `curl -s http://127.0.0.1:8765/api/ping`. Adds the recovery path for
  skipping Step 1, the GUI equivalent, and an uninstall command.
- **DMG `Read Me.txt` rewritten as a numbered walkthrough.** It now names the
  three items in the mounted volume, says the DMG window stays open after the
  drag, tells the user to eject, and gives the ping check that proves the app is
  running.
- **Stale `2.0.1` references** in the README and RUNBOOK §4.7 corrected to
  `2.0.2`.

## [2.0.2] — 2026-11-25

Documentation and packaging fix. `2.0.1` shipped a `Read Me.txt` that told users
to drag the app and then allow it afterwards, which is the step that fails on
macOS 15+. The DMG now leads with clearing quarantine **before** installing.

### Changed

- **DMG `Read Me.txt` rewritten.** It now opens with an *IMPORTANT — read this
  before double-clicking* block giving the `xattr -dr com.apple.quarantine` step
  on the DMG, the same step on the installed app if it was already dragged, the
  Open Anyway fallback, and the `curl` installer as the route that avoids the
  dialog entirely.
- **Install docs lead with the prompt-free route.** README, RUNBOOK §4.7 and
  ADR-007 put the installer first, document clearing quarantine on the DMG before
  installing, and record what does *not* work: Homebrew casks still apply
  quarantine, and a self-signed certificate is still rejected by `spctl` even
  when trusted.

### Added

- **One-line installer** — `scripts/install.sh` downloads the latest DMG with
  `curl`, verifies its SHA-256 against `release/latest.json`, installs to
  `/Applications`, checks the signature and clears quarantine. `curl` sets no
  quarantine flag, so macOS 15+ never shows the "Not Opened" dialog and no Apple
  Developer account is needed. Supports `--version`, `--dir` and `--no-launch`.
- **SA-16 installer safety** — the audit now checks that the installer parses,
  verifies a checksum and the signature, clears quarantine on the artifact it
  verified, and never pipes a download into a shell.

## [2.0.1] — 2026-11-25

Security release. The published `2.0.0` DMG was built from `c421803`, before the
security audit fixes landed, so it still shipped the unauthenticated `/api/quit`
endpoint and the prefix-based static path check. This release republishes the
hardened tree and adds the log folder, the model fit catalogue and the background
API server.

### Security

- **`/api/quit` removed.** It was unauthenticated and shut the app down; it now
  returns `404`. Fixed in `799b22e`, shipped here for the first time.
- **Static path traversal closed by construction.** The request path is a key into
  a map built by scanning `ui/` at startup, so no filesystem path is derived from
  user input. Replaces the `str.startswith` check that allowed
  `/../ui-evil/leak.txt` to return `200`. Clears CodeQL alerts #1–#3
  (`py/path-injection`). Fixed in `799b22e`.
- **Body size limit.** `Content-Length` is parsed defensively and capped at 8 MiB,
  so an oversized or malformed header no longer raises a traceback.
- **Client disconnects** are treated as normal rather than server errors.

### Added

- **Log folder** — everything now lives in `~/.mlx_studio/logs/`
  (`activity.log`, `app.log`, `launch.log`, `server.log`), rotated at 2 MB with
  three backups. Logs from 2.0.0 are migrated on first start.
- **Export diagnostics** — Activity → Export diagnostics downloads a zip of the
  log folder plus `environment.json` (versions, paths, hardware, installed model
  ids) so an end user can attach one file to a GitHub issue.
- **Application log** — `app.log` records startup, shutdown, HTTP errors, model
  load failures, download failures, install results and API server deaths.
- **Best for your Mac** — a curated 25-model catalogue in the Download view,
  each rated `great fit` / `good fit` / `tight fit` / `too large` against this
  machine's unified memory using measured 4-bit weight sizes. Filter by fit band
  or category.
- **Background API server** — a **Background** checkbox detaches `mlx_lm server`
  into its own session so it keeps serving after the window closes. The pid is
  written to `~/.mlx_studio/server.pid` and adopted on the next launch.
- **Server down notification** — a 2 s watchdog detects the server dying, shows a
  red banner with the exit status and a **Restart** button, fires a desktop
  notification, and logs `api server died`.

### Changed

- **Install instructions corrected for macOS 15+.** Apple removed the
  Control-click → Open bypass in Sequoia, so the documented step did nothing and
  the app looked broken. The README, runbook, ADR-007, PRD, security docs and the
  DMG `Read Me.txt` now lead with **System Settings → Privacy & Security → Open
  Anyway**, keeping right-click → Open for macOS 14 and older, and document the
  `xattr -dr com.apple.quarantine` alternative.

### Fixed

- A server restart no longer lets the old watcher delete the new pidfile or
  raise a false "stopped unexpectedly" banner.
- HTTP error logging now coerces the status code, which
  `BaseHTTPRequestHandler` passes as a string.
- The watchdog polls the `Popen` handle rather than the pid, because a detached
  child stays a zombie and `os.kill(pid, 0)` still succeeds on one.

## [2.0.0] — 2026-11-24

First packaged release. Replaces the earlier Tk prototype with a web-technology
interface hosted in a native macOS window.

### Added

- **Native desktop app** — pywebview window (1360×900) with browser fallback.
- **Chat** — in-process model load/unload, token streaming over SSE, per-turn
  metrics (`tokens · tok/s · GB peak`), Stop and New chat controls.
- **Models** — cards per cached model with size, Load/Unload, Reveal, Delete
  (blocked while loaded).
- **Download** — search the `mlx-community` org on Hugging Face, live per-model
  download status.
- **API Server** — one-click `mlx_lm server` on `http://127.0.0.1:8080/v1`, live
  output pane, copyable endpoint, `curl` example.
- **Activity** — filterable, colour-coded log persisted to
  `~/.mlx_studio/activity.log`.
- **Setup** — installed versions of MLX / MLX-LM / huggingface-hub with
  `up to date` / `update → x.y.z` / `missing` pills, app version, hardware,
  last-check time, and a nav `!` badge when anything is outdated.
- **App update channel** — `MLX_STUDIO_UPDATE_URL` manifest support with an
  in-app *App update available* panel and DMG download button.
- **Packaging** — `packaging/build_app.sh` builds a signed `.app` and a
  compressed `.dmg`; `packaging/release_manifest.py` generates the update
  manifest with SHA-256.
- **App icon** — rendered 1024 px source compiled to `AppIcon.icns`.
- **Docs** — README with screenshots, LICENSE, CHANGELOG, `.gitignore`.

### Changed

- Model sizes now follow symlinked blobs and de-duplicate by inode, so a 4 GB
  model reports 4.0 GB instead of 8.0 GB.
- Server stop is logged exactly once, by the output pump that observes exit.

### Fixed

- Cross-thread Tk callbacks crashed with `RuntimeError: main thread is not in
  main loop`; worker updates now go through a queue drained on the main thread.
- PyPI update checks failed with `CERTIFICATE_VERIFY_FAILED` on python.org
  builds; `truststore` is now injected before the request.

### Removed

- `MLX_Model_Manager.py` (Tk prototype) — superseded by `app.py` + `ui/`.

## [1.0.0] — 2026-11-24

- Initial Tkinter prototype: install MLX, download/delete models, launch
  `mlx_lm chat` in Terminal, run the API server, basic activity log.

[2.0.8]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.8
[2.0.6]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.6
[2.0.5]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.5
[2.0.4]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.4
[2.0.3]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.3
[2.0.2]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.2
[2.0.1]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.1
[2.0.0]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.0
[1.0.0]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v1.0.0
