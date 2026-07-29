# Daemonization — macOS packaging, IPC, and keystore hardening for `keriguard_user`

This document covers the daemonization work — turning sentinel/guardian into independent, launchd-supervised macOS background daemons — plus the cleanup items deferred out of the packaging pipeline.

> **Phase numbering note:** This document was renumbered to be internally consistent. Phases are ordered by execution sequence, and every cross-reference below points at the phase number as it appears in *this* document.

---

## Prerequisite state — do NOT re-verify

The following are already complete and are assumed as the starting point for this plan:

- **End-to-end provisioning verification** against the docker-hosted platform: complete (the provisioning *mechanism* is proven end-to-end). **Note:** this was verified with the old `witness=False` default. Per the Witness decision below, the dev environment is **wiped and re-provisioned from scratch with `witness=True`** — this is a fresh first-time provisioning, **not** a migration of an existing AID.
- **`feat-keriguard-build` packaging pipeline** (`Locksmith.spec`/PyInstaller; `Locksmith.app` launches, opens a vault, runs Setup end-to-end): recently built and verified from source.

**Gate:** Do **not** re-verify the packaging build until the daemonization work below — Phase 4 in particular, which adds new PyInstaller targets and entitlements to the same spec/sign pipeline — is substantially in place. Re-verifying now would just re-verify the same build again before it changes.

## Dependency graph (critical path)

    Decisions (all settled: headDirPath=--data-dir; witness=YES; sentinel=SaaS/--config)
            │  ← all three decisions RESOLVED; proceed straight to Phase 1
            ▼
    Phase 1 (upstream patches — CODE COMPLETE on `daemonize-phase1`; review + tag remain)
            │
            ├───────────────────────┐
            ▼                        ▼
    Phase 3 (plugin daemon code)   Phase 2 (pkg cleanup) ─┐
            │                                             │ ← Phase 2 & Phase 4
            │                        Phase 4 (PyInstaller │   done in ONE pass
            │                        + sign + pin) ───────┘   (shared files)
            └───────────┬────────────────────┘
                        ▼
    Phase 5 (verify: cleanup → lifecycle → e2e)

**Sequencing rules baked into the graph:**

1. **The reconciliation decisions are now all resolved** — the `headDirPath` → `--data-dir` flag, witness = YES, and SaaS mode are decided in the Decisions section below. No open blocker remains before Phase 1.
2. **Phase 1 must land + tag before Phase 4** — pinning needs tags; packaging needs the socket/passcode/heartbeat/`--data-dir` patches. **The code for 1a–1d is complete** (branch `daemonize-phase1`: `sentinel` `3b67423`, `keriguard` `56ee181`); what remains on the critical path is the fork+review pass, merge to `main`, and tagging.
3. **Phase 2 and Phase 4 are one pass, not two phases run back-to-back** — they touch the same three files (`Locksmith.spec`, `sign.sh`, `release.ci.yml`); doing them in a single pass avoids re-touching entitlements/signing twice. Phase 3 also feeds Phase 4, but as an ordinary dependency (its outputs are consumed), *not* as a shared-file pairing.
4. **The three `/tmp` socket sites move in lockstep** within a single Phase 1 PR.
5. **Do not re-verify the packaging build** until Phase 4 is substantially in place (see gate above).

---

## Decisions

### Two separate daemons (recorded, not yet implemented)

**Two separate long-running processes**, running (macOS-adapted) `kg guardian start` and `sentinel start` independently, mirroring the proven Linux split:

- `keriguard/debian/keriguard-guardian.service` — `ExecStart=.../kg guardian start -c ${KERIGUARD_CONFIG}`, `Restart=on-failure`, `RestartSec=10`.
- `keriguard/debian/keriguard-sentinel.service` — `ExecStart=.../sentinel start -c ${SENTINEL_CONFIG}`, same restart policy.

This reuses both CLIs' tested code paths unchanged, keeps failure domains independent (a WireGuard-apply bug in guardian doesn't take down KEL-watching), and the two already coordinate only through on-disk exported CESR files (`sentinel/src/sentinel/framework/watching.py:76-127`) and a Unix socket (Phase 3b) — a boundary that fully decouples them. No code has been written for either daemon's macOS packaging yet; that's Phase 3/Phase 4.

### `headDirPath` gap (Risk #5) — DECIDED: **add a `--data-dir` flag to both CLIs**

`plugins/user/src/keriguard_user/core/provisioning.py`'s `_open_or_create_hab()` already provisions under `headDirPath = ~/Library/Application Support/KERIGuard/keystores`, `base = ""` (a sibling of the human vault's own LMDB tree, so deleting the Locksmith vault doesn't orphan/break a still-running daemon, and vice versa).
(Note: this helper lives in `provisioning.py`, not `core/keystore.py` — `keystore.py` only provides `keystores_dir()`, `generate_bran()`, and `load_or_create_bran()`.)

**Correction (verified against current `provisioning.py`): the two Haberies are NOT distinguished by `base`.** `bootstrap_server_identity()` calls `_open_or_create_hab(base_dir, bran, name, alias)` for the guardian Habery and `_open_or_create_hab(base_dir, bran, f"{name}-sentinel", f"{alias}-sentinel")` for the sentinel Habery — **both** invocations hardcode `base=""` inside `_open_or_create_hab`. The actual distinguishing path segment (hio's `Filer.reopen` composes `headDirPath/tailDirPath/base/name`) is **`name`**, not `base`. Since a Habery corresponds 1:1 to an LMDB keystore, guardian and sentinel still each open their **own Habery / own LMDB environment** — that part of the design holds — but the mechanism is `--name`/`--alias`, not `--base`. This keeps the "coordinate only via exported CESR + the Phase 3b Unix socket" boundary honest: there is no shared-LMDB concurrent-writer contention between the two daemons.

**No new CLI flag needed for this piece** — `--name`/`-n` and `--alias` already exist on both `sentinel start` and `kg guardian start`/`up.py` today. Each daemon's launchd `ProgramArguments` (Phase 3c) must pass explicit `--name`/`--alias` values matching what provisioning wrote for that daemon's Habery (guardian: `{vault}-server` / sentinel: `{vault}-server-sentinel`, given the vault's own name/alias) — see the `--name` note in Phase 1c/3c. Relying on either CLI's default `--name` would open the wrong (default) Habery, not the provisioned one.

**Gap that *does* require a new flag**: neither `sentinel start` (`sentinel/src/sentinel/app/cli/commands/start.py`) nor `kg guardian start` (`keriguard/src/keriguard/app/cli/commands/guardian/up.py:139,155`) exposes a `headDirPath`/custom-data-dir flag — only a relative `base` (hardcoded default, not exposed as a distinguishing flag per the correction above). A Habery provisioned under the sandbox-friendly absolute App-Support path **cannot be reopened by an unmodified `sentinel start` / `kg guardian start` daemon**, regardless of `--name`.

**Decision: patch both upstream CLIs to accept a `headDirPath` override via a new `--data-dir` flag** (option (a)). This preserves the App-Support isolation `provisioning.py` already relies on — the daemon reopens the exact Habery that provisioning created, rather than falling back to a default location.

Consequences baked into the rest of this plan:
- Both `sentinel start` and `kg guardian start` gain a `--data-dir <absolute path>` flag that sets the Habery `headDirPath` (Phase 1c — a **required** Phase 1 item).
- Both daemons' launchd `ProgramArguments` (Phase 3c) pass `--data-dir ~/Library/Application Support/KERIGuard/keystores` **plus explicit `--name`/`--alias`** matching provisioning's values, so they reopen the provisioned Habery.
- The `--data-dir` path is already inside the App-Support tree covered by the Phase 2b / Phase 4 entitlements audit.

### Witness for the guardian AID — DECIDED: **YES, the guardian AID gets a witness**

Witness provisioning for a server AID is a separate OTP-based flow (`reserve_witness_for_server` → OOBI-load → `authenticate_witness` → `rotate_witness`), **not** a `number_of_witnesses` field on the registration `doc`. The current code defaults `witness=False`, `toad=0`; **this default is being overridden — the guardian AID will be provisioned with a witness.**

Consequences baked into the rest of this plan:
- Provisioning must run the full OTP-based flow via `provisioning.py`'s `bootstrap_server_identity` (`reserve_witness_for_server` → OOBI-load → `authenticate_witness` → `rotate_witness`) with `witness=True` and a non-zero `toad`, **before** Phase 4 packages the daemon. In this dev environment this is a **from-scratch provisioning after wiping the environment** — there is no existing `witness=False` AID to migrate.
- The guardian launchd `ProgramArguments` (Phase 3c) reflect the witnessed shape (`witness=True` two-Habery bootstrap).
- The **OTP-secret path** needs a defined macOS location. Store it alongside the other daemon secrets under `~/Library/Application Support/KERIGuard/` (e.g. `~/Library/Application Support/KERIGuard/server.otp`, mode `0600`, owner-only, matching the `server.bran` convention in Phase 1b), and include it in the entitlements audit (Phase 2b / Phase 4).

### Sentinel mode — DECIDED: **SaaS mode (`--config <yaml>`)**

`sentinel start` **hard-requires `--config <yaml>` in SaaS mode.** Confirmed at `sentinel/src/sentinel/app/cli/commands/start.py`: `merge_config_and_args()` (line 107) raises `ValueError("SaaS mode (local: false) requires --config")` at line 134 when `--local` is unset, and `server_name`/`server_alias` can't be passed as flags in that mode at all.

**Decision: the sentinel daemon runs in SaaS mode** (plugin `credential_source = "healthKERI"`). This means the launchd plist **must not** use flag-based invocation; it points `--config` at a generated YAML file.

Consequences baked into the rest of this plan:
- `sentinel_launch.py` (Phase 3a) must **render a YAML config file from the plugin's settings and write it to disk before writing the plist.** Store the generated YAML under the daemon's App-Support tree (e.g. `~/Library/Application Support/KERIGuard/sentinel/config.yaml`) so it lives with the other sentinel daemon state and is covered by the same entitlements.
- The sentinel plist's `ProgramArguments` are `sentinel start --config ~/Library/Application Support/KERIGuard/sentinel/config.yaml --data-dir ~/Library/Application Support/KERIGuard/keystores --uxd --export-dir <export_dir>` (plus `--passcode-file` per Phase 1b). **No `--name`/`--alias`/`--local` flags** — those are rejected in SaaS mode and are instead expressed inside the generated YAML.
- Add the generated `config.yaml` path to the entitlements audit (Phase 2b / Phase 4) and the socket/file inventory (Phase 3d).

---

## Phase 1 — Upstream repo patches (`sentinel` + `keriguard`) [CODE COMPLETE — review + tag pending]

Group these — they touch the same upstream files, and Phase 4 depends on all of them landing **and being tagged**. These are patches to the `keriguard`/`sentinel` repos, **not** `keriguard-plugin`.

> **Status (implemented, not yet merged/tagged):** All four sub-items (1a–1d) are implemented and unit-tested on branch `daemonize-phase1` in both `sentinel` (commit `3b67423`) and `keriguard` (commit `56ee181`), both still on top of `main` in each repo. Full test suites pass in both repos (`sentinel`: 227 passed; `keriguard`: 176 passed), including new tests for the `socket_dir`/`head_dir_path`/`--passcode-file`/heartbeat behavior. **Deliberately not tagged yet** — per the user's request, these branches should go through a fork+review pass first so `main` in either upstream repo isn't tagged before the daemonization approach is confirmed end-to-end. Tagging (needed for Phase 4 pinning) is still an open step once that review lands. Two follow-ups noticed but out of scope for Phase 1: (1) `sentinel`'s venv was missing `pytest-asyncio` despite being a declared dependency — installing it fixed 14 unrelated pre-existing test failures, a pure local-env fix, no code change; (2) `keriguard`'s venv was missing `pyotp` (a `sentinel` dependency) — also a local-env-only fix needed to import `guardian/up.py` for manual CLI verification.
>
> **Two implementation details that ripple into later phases:**
> 1. The socket relocation is exposed as a **`--socket-dir` flag on `sentinel start`** (default `/tmp`), so the macOS override is a plist argument, not a compiled-in constant — Phase 3a/3c must emit it, and it belongs in the Phase 3d inventory.
> 2. `keriguard`'s `--data-dir` is threaded through **`kg guardian up` as well as `kg guardian start`**, and is **persisted into the generated guardian YAML** (`generate_guardian_config` / `KERIGuardConfig`), so `up` can hand it to `start --config`. The guardian daemon can therefore inherit `data_dir` from its config file; Phase 3c still passes it explicitly so the plist is self-describing and can't drift from a stale YAML.

### 1a. Sentinel watcher-add socket — relocate off `/tmp` (Risk #1)  [COMPLETE]

`sentinel watcher add` talks to a running `sentinel start --uxd` process via `LocalWatcherConnector` over a Unix socket (`sentinel/src/sentinel/framework/watching.py:227-308`, confirmed). Both client and server hardcode `f"/tmp/sentinel_{hab.pre}.sock"`.

Thread a `socket_dir` parameter through **all three lockstep sites**, defaulting to `/tmp` for existing Linux/Debian packaging, overridden to `~/Library/Application Support/KERIGuard/sentinel/` for the macOS launchd agent (matching the existing `~/Library/Application Support/KERIGuard/helper.sock` convention):
- client — `watching.py:250`
- server (`setup_local`) — `sentineling.py:106` (file confirmed at `sentinel/app/sentineling.py`, not `framework/`)
- server (`setup_hk`) — `sentineling.py:186`

> **Note (SaaS mode):** since the daemon runs SaaS mode, the active server-side socket setup is `setup_hk` (`sentineling.py:186`), not `setup_local`. Both sites still move in lockstep, but `setup_hk` is the one exercised at runtime by this daemon — verify the relocated path via the `setup_hk` path in Phase 5.

Coordinate the parameter threading and the macOS override value together in **one PR** against `sentinel`.

**Implemented (`sentinel` `3b67423`):** a `socket_dir` parameter is threaded through `LocalWatcherConnector`, `watcher/add.py`, `setup_local`, and `setup_hk`, and surfaced as a **`--socket-dir` flag on `sentinel start`** defaulting to `/tmp` — existing Linux/Debian behavior is byte-for-byte unchanged when the flag is omitted. The macOS value (`~/Library/Application Support/KERIGuard/sentinel/`) is supplied by the launchd plist (Phase 3c), and the plugin's direct `LocalWatcherConnector` use (Phase 3a) must pass the same `socket_dir` or it will look for the socket in `/tmp`.

### 1b. Add `--passcode-file` to both CLIs  [COMPLETE]

`core/keystore.py`'s `load_or_create_bran()` already stores the bran at `~/Library/Application Support/KERIGuard/server.bran` (mode `0600`, `stat.S_IRUSR|S_IWUSR`, owner-only), independent of the human vault's own passcode. **The bran file *is* the passcode file** — the daemon's passcode is exactly the contents of `server.bran`, so `--passcode-file` points at that same file (there is no second, separate passcode secret). **But** neither daemon CLI has a `--passcode-file` option today (guardian `start.py`: only `--base`/`-b` at line 75 and `--passcode` at lines 78-82; sentinel `start.py`: only `--passcode`/`-p` at lines 33-38) — a plain string visible via `ps`.

Add a `--passcode-file` flag to both:
- `keriguard/src/keriguard/app/cli/commands/guardian/start.py`
- `sentinel/src/sentinel/app/cli/commands/start.py`

**Read semantics:** the `--passcode-file` reader must `.read().strip()` the file (strip trailing newline/whitespace) and decode as UTF-8, symmetric with how `load_or_create_bran()` writes `server.bran`. A stray trailing `\n` or an encoding mismatch produces a "wrong passcode" failure that looks unrelated to the file — pin the write/read convention on both sides so it can't drift.

**Implemented (`sentinel` `3b67423`, `keriguard` `56ee181`):** `--passcode-file` added to `sentinel start` and `kg guardian start`; the reader reads and strips the file as specified. **Precedence: `--passcode` wins if both are given** — so a plist that passes `--passcode-file` must not also pass `--passcode`, or the file is silently ignored.

Flag Keychain storage as a v2 hardening step once the upstream CLIs support pluggable passcode sources.

### 1c. Add a `--data-dir` (`headDirPath`) flag to both CLIs — REQUIRED  [COMPLETE]

Per the `headDirPath` decision above, patch both upstream CLIs to accept an absolute `headDirPath` override via a new `--data-dir` flag, so a Habery provisioned under the App-Support path can be reopened by the daemon:
- `sentinel/src/sentinel/app/cli/commands/start.py`
- `keriguard/src/keriguard/app/cli/commands/guardian/up.py` (the `--base`-only bootstrap at `:139,155` — `Habery(name=keriguard_name, base=args.base, ...)` / `Habery(name=sentinel_name, base=args.base, ...)`)

The flag sets the Habery `headDirPath`; `--base` continues to be the relative sub-tree under it, preserving existing Linux/Debian behavior when `--data-dir` is omitted (default/fallback location unchanged).

**Implemented:**
- `sentinel` (`3b67423`) — `--data-dir` on `sentinel start`, threaded into `Habery`/`SentinelBaser` as `headDirPath`; when omitted it falls back to the old base-as-`headDirPath` behavior, so Linux/Debian is unchanged.
- `keriguard` (`56ee181`) — `--data-dir` on `kg guardian start` **and** `kg guardian up`, threaded into both bootstrap Haberies and `KERIGuardBaser`, and written into the generated guardian YAML via `generate_guardian_config`/`KERIGuardConfig` so `up` can hand it to `start --config`.

**`--name`/`--alias` contract (required for the distinct-Habery design — corrected from an earlier `--base`-based draft of this plan):** `provisioning.py`'s `_open_or_create_hab()` hardcodes `base=""` for **both** Haberies — verified directly against current source. The two Haberies are actually distinguished by `name`/`alias`: guardian uses `name`/`alias` as provisioned (the vault's own name/alias), sentinel uses `f"{name}-sentinel"`/`f"{alias}-sentinel"`. Both `sentinel start` and `kg guardian start`/`up.py` **already expose `--name`/`-n` and `--alias` flags today** — no CLI patch needed for this piece. Each daemon's launchd `ProgramArguments` (Phase 3c) must pass **explicit `--name`/`--alias`** matching what `provisioning.py` used for that daemon's Habery — do **not** rely on either CLI's default `--name`. A mismatch silently opens an empty/wrong Habery rather than the provisioned one (caught by the Phase 5 "reopen via `--data-dir`, no fallback" check).

### 1d. Guardian heartbeat touch  [COMPLETE]

Guardian has no local control/status socket on any platform today (Linux uses systemd D-Bus, which has no macOS analog). Adding a new status socket would mean modifying `sentinel.framework.runner.run()` (`sentinel/src/sentinel/framework/runner.py:19`→EOF — a synchronous entry point that optionally builds a `Habery`/`AppBaser`, then `asyncio.run(_async_run(...))`, which creates a `FileWatchingService`, starts it as a task, and blocks until `SIGINT`/`SIGTERM` or task completion) to run a second listener — new, unproven code. Instead, use a heartbeat file.

`keriguard/src/keriguard/app/sentinel/handler.py`'s `KeriguardEventHandler` has **no** single per-poll-cycle "success" callback — it exposes three separate handlers, `on_kel` (line 45), `on_tel` (line 68), `on_credential` (line 89).

**Preferred**: add the heartbeat touch inside `runner.py`'s `_async_run`/`FileWatchingService` poll loop, at the point it confirms a full cycle completed without exception (reflects real service health, not "some event fired"). Read `FileWatchingService`'s poll-loop structure first to pick the exact point. Fallback: touch at the end of all three handler methods.

Heartbeat path: `~/Library/Application Support/KERIGuard/guardian.heartbeat`.

**Implemented (`sentinel` `3b67423`) — the preferred option, not the handler fallback:** a `heartbeat_path` parameter was added to `FileWatchingService`, `runner.run()`, and `_async_run()`, and the file is touched after each poll cycle completes without raising. `handler.py`'s three per-event handlers are untouched. Consequence for Phase 3: staleness thresholds in `guardian_check.py` must be derived from the poll interval (a healthy but idle daemon still touches the file every cycle), and the heartbeat is written by the **sentinel-framework runner the guardian embeds**, so the path must be passed in from the guardian invocation rather than assumed.

---

## Phase 2 — Packaging pipeline cleanup [2a/2b/2c CODE COMPLETE — tagging + real sandboxed smoke test pending]

These three items were scoped out of the packaging pipeline so build-verify/merge-to-`locksmith`-main wouldn't block on them. **Do them together with Phase 4**, since Phase 4 touches the same files (`Locksmith.spec`, `sign.sh`, `release.ci.yml`) again to add the two new daemon executables — one pass avoids touching entitlements/signing twice.

> **Status:** All three sub-items (2a–2c) are implemented.
> - `keriguard-plugin` (this repo, commit `e59027a` on `feat-daemonization`): `plugins/user/pyproject.toml` and `plugins/admin/pyproject.toml` bumped `0.0.1` → `0.1.0`; added `.github/workflows/ci.yml` (installs both plugins into a clean venv, import-smoke-tests `keriguard_user`/`keriguard_admin`). **Note:** `keriguard-admin` depends on `locksmith==0.0.1`, which is *not* the PyPI package of that name (PyPI has an unrelated `locksmith` at versions 0.1–1.1) — the CI workflow installs the real `locksmith` from `git+https://github.com/healthKERI/locksmith.git` before installing the admin plugin so the pin resolves correctly instead of failing or grabbing the wrong package. `release.ci.yml`'s own plugin-flavor job has the same latent issue but is out of scope for this pass (it already runs `pip install -e .` inside the `locksmith` checkout, which happens to satisfy it there).
> - `locksmith` (uncommitted local edits, not yet committed/pushed — pending your review): `.github/workflows/release.ci.yml`'s plugin-flavor job now pins both plugin installs to `@v0.1.0` instead of the floating default-branch URL; `scripts/sign.sh`'s heredoc that regenerated an independent `entitlements.plist` copy is removed, so the existing `--entitlements entitlements.plist` codesign call now signs against the single checked-in file; `entitlements.plist` gained `com.apple.security.files.user-selected.read-write` plus a `com.apple.security.temporary-exception.files.absolute-path.read-write` array covering `~/Library/Application Support/KERIGuard/` and `~/.locksmith/` (folding in the union of what the old heredoc granted plus the new paths from the Decisions section); `scripts/fetch_keriguard_helper.py` now requires `KERIGUARD_HELPER_VERSION` to be set and exits 1 with an error instead of defaulting to `"current"`.
>
> **Still open:**
> 1. **Tag `v0.1.0` doesn't exist yet** — `release.ci.yml`'s pin references it but nothing has been tagged/pushed (deliberately deferred, matching the Phase 1 fork+review-before-tagging pattern). Cut the tag once this pass is reviewed.
> 2. **The `locksmith` edits above are local/uncommitted** — not yet committed or pushed to `arilieb/locksmith`, pending review.
> 3. **Final sign-off is still deferred to Phase 5** (per the existing note below) — none of 2b's entitlements changes have been exercised under a real sandboxed build yet.

### 2a. Pin the plugin dependency (Risk #2)

`locksmith/.github/workflows/release.ci.yml`'s plugin-flavor build job installs `keriguard-user`/`keriguard-admin` via two unpinned `pip install git+https://github.com/healthKERI/keriguard-plugin.git#subdirectory=plugins/{user,admin}` calls (`release.ci.yml:209-210` — no explicit `@main`, but a bare URL floats to the default branch, same unpinned effect). `keriguard-plugin` still has zero git tags and zero CI.
- Bump `plugins/user/pyproject.toml` and `plugins/admin/pyproject.toml`'s static `version = "0.0.1"` and start tagging releases (semver).
- Pin `release.ci.yml`'s install step to a tag instead of `@main`.
- Add a minimal CI workflow to `keriguard-plugin` (no `.github/workflows/` directory exists yet) — at minimum, install both plugin packages into a venv and run an import smoke test (`import keriguard_user`, `import keriguard_admin`) on push/PR.

### 2b. Fix the entitlements duplication + gap

`locksmith/scripts/sign.sh` does not read the checked-in `locksmith/entitlements.plist` — it regenerates an independent copy via a heredoc (`sign.sh:101-126`) and signs against that copy, so the two can silently drift.
- Change `sign.sh` to pass the checked-in `entitlements.plist` path directly to `codesign --entitlements` (remove the heredoc).
- `entitlements.plist` declares `app-sandbox=true` plus `network.client`/`network.server` but no `com.apple.security.files.*` entitlement, despite `KERIGuardUserBaser`'s LMDB store, the user-chosen WireGuard config directory, the `~/Library/Application Support/KERIGuard/` keystore/bran files, and `~/.locksmith/*.pem` all doing file I/O outside the sandbox container. Add the needed entitlement (`com.apple.security.files.user-selected.read-write` at minimum; the App-Support and `~/.locksmith` paths are outside the container and outside user-selection, so they likely also need a `com.apple.security.temporary-exception.files.absolute-path.read-write` array entry, or relocating those paths under the app's sandbox container).
- **Also cover the paths introduced by the Decisions section:** the `--data-dir` keystore tree (`~/Library/Application Support/KERIGuard/keystores/`), the guardian OTP-secret file (`~/Library/Application Support/KERIGuard/server.otp`), and the generated sentinel SaaS config (`~/Library/Application Support/KERIGuard/sentinel/config.yaml`).
- **Final sign-off is deferred to Phase 5** (requires a real sandboxed smoke test — this has never been exercised under a real sandboxed build).

### 2c. Fix the `KERIGUARD_HELPER_VERSION` default (Risk #3)

`locksmith/scripts/fetch_keriguard_helper.py:101` defaults to the literal string `"current"` if the env var is unset — an implicit-latest footgun for a security-sensitive embedded binary that owns the WireGuard tunnel. Make it a required, non-defaulted CI input (raise/exit if unset rather than defaulting).

---

## Phase 3 — New plugin daemon code (`keriguard-plugin`)

In `plugins/user/src/keriguard_user/core/`, following the existing `helper_launch.py`/`helper_check.py` template:
- `launch_helper_app()` (`helper_launch.py:17`) runs `subprocess.Popen(["open", "-a", str(app_path)])` (line 40) to launch `KERIGuardHelper.app`.
- `is_helper_installed()` (`helper_check.py:23`) runs `launchctl print gui/{uid}/{HELPER_AGENT_LABEL}` and checks the returncode for a health check.

### 3a. New files

- `sentinel_launch.py` / `guardian_launch.py` — write the filled-in launchd plist and run `launchctl bootstrap gui/$(id -u) <plist>` for each daemon.
  - **`sentinel_launch.py` (SaaS):** first render a YAML config file from the plugin's settings and write it to `~/Library/Application Support/KERIGuard/sentinel/config.yaml`, **then** write the plist whose `ProgramArguments` point `--config` at that file (and pass `--data-dir` and `--socket-dir`). Do not emit `--name`/`--alias`/`--local` (rejected in SaaS mode).
  - **`guardian_launch.py` (witnessed):** the plist's `ProgramArguments` reflect the `witness=True` two-Habery bootstrap and pass `--data-dir`; ensure the OTP-secret at `~/Library/Application Support/KERIGuard/server.otp` exists (mode `0600`) before bootstrapping. If the guardian is launched with `--config`, the YAML generated by `kg guardian up` already carries `data_dir` (Phase 1c) — pass `--data-dir` explicitly anyway so the plist doesn't depend on a stale generated config.
- `sentinel_check.py` / `guardian_check.py` — health checks, following the `is_helper_installed()` pattern (`launchctl print gui/<uid>/<label>`, plus, for guardian, the heartbeat-file staleness check from Phase 1d — threshold derived from the poll interval, since the touch is per poll cycle, not per event).
- Bundled launchd plist templates under a new `plugins/user/resources/launchd/` (`com.healthkeri.keriguard.sentinel.plist`, `com.healthkeri.keriguard.guardian.plist`).
- Have the plugin call `LocalWatcherConnector` directly (or a thin wrapper) rather than shelling out to `sentinel watcher add` — passing the same `socket_dir` the daemon was started with (Phase 1a), not the `/tmp` default.

### 3b. Plain frozen CLI binaries, not signed `.app` bundles

`KERIGuardHelper.app` needs the full Xcode/System-Extension/notarization pipeline because it owns the WireGuard tunnel via `NEPacketTunnelProvider`. `kg guardian start` never touches the tunnel directly — it delegates tunnel control to KERIGuardHelper's existing Unix-socket IPC. `keriguard/src/keriguard/core/systeming.py`'s `_send_helper_request` (~line 164) already implements this: line-delimited JSON over a Unix socket at `_HELPER_SOCKET_PATH` (`~/Library/Application Support/KERIGuard/helper.sock`, matching `IPCServer.defaultSocketPath()` in the separate `keriguard-helper` Swift project). So freeze the two new daemons with PyInstaller and sign them with the same `codesign` loop `sign.sh` already runs — no new Xcode pipeline, no new notarization pipeline.

### 3c. Supervision — bundled launchd agents, not `SMAppService`

`SMAppService.agent(plistName:)` is Swift/`ServiceManagement`-only and requires a `.app` bundle identity these daemons don't have. Ship plist templates as bundled resources; at first successful provisioning (already complete), `sentinel_launch.py`/`guardian_launch.py` write the filled-in plist (executable path, `--data-dir`, `--passcode-file` per Phase 1b, plus the SaaS `--config` YAML for sentinel) to `~/Library/LaunchAgents/com.healthkeri.keriguard.{sentinel,guardian}.plist` and run `launchctl bootstrap gui/$(id -u) <path>`. `RunAtLoad=true` plus `KeepAlive` gives crash-restart behavior matching the Debian units' `Restart=on-failure`.

`ProgramArguments` mirror the existing CLI invocations, reflecting the settled decisions:
- **Guardian (witnessed; `--data-dir` + explicit `--name`/`--alias`):** `kg guardian start --data-dir ~/Library/Application Support/KERIGuard/keystores --name <guardian_name> --alias <guardian_alias> --sentinel-aid <sentinel_hab.pre> --sentinel-export-dir <export_dir> --passcode-file ~/Library/Application Support/KERIGuard/server.bran ...` (two-Habery bootstrap shape — `server_hab`/`sentinel_hab`/`witness=True` — confirmed at `keriguard/src/keriguard/app/cli/commands/guardian/up.py:244-248`, which already matches the witnessed shape).
- **Sentinel (SaaS; `--data-dir` + explicit `--name`/`--alias`):** `sentinel start --config ~/Library/Application Support/KERIGuard/sentinel/config.yaml --data-dir ~/Library/Application Support/KERIGuard/keystores --socket-dir ~/Library/Application Support/KERIGuard/sentinel --name <sentinel_name> --alias <sentinel_alias> --uxd --export-dir <export_dir> --passcode-file ~/Library/Application Support/KERIGuard/server.bran`. **No** `--local` — SaaS mode lives in the generated YAML (per the Decisions section), but `--name`/`--alias` **are** required here, unlike flag-based sentinel invocations, precisely because they can't be expressed inside that YAML.

> **Note (corrected from an earlier `--base`-based draft):** `<guardian_name>`/`<guardian_alias>` and `<sentinel_name>`/`<sentinel_alias>` are the actual Habery-distinguishing values under the shared `--data-dir` `headDirPath` — `provisioning.py` hardcodes `base=""` for both Haberies and instead names them `name`/`alias` (guardian) and `f"{name}-sentinel"`/`f"{alias}-sentinel"` (sentinel). Both CLIs already support `--name`/`--alias` today; they must be passed explicitly (matching what provisioning wrote) rather than defaulted.
> **Note:** `--passcode-file` points at `server.bran` because the bran file *is* the passcode file (Phase 1b) — both daemons read the same file. This is intentional, not a copy-paste of two distinct secrets. Do **not** also pass `--passcode`: it takes precedence and would silently shadow the file.
> **Note:** `--socket-dir` must be passed explicitly on the sentinel invocation — Phase 1a kept the flag's default at `/tmp` so Linux/Debian behavior is unchanged, which means omitting it on macOS silently reverts to the `/tmp` socket. 
> **Note:** the daemon `kg` we freeze is plain `kg guardian start` (`kg = "keriguard.app.cli.kg:main"`), **not** the unrelated `kg-sentinel = "keriguard.app.sentinel.main:main"` entry point.

### 3d. Resulting socket/file inventory

- `~/Library/Application Support/KERIGuard/helper.sock` — existing, unchanged.
- `~/Library/Application Support/KERIGuard/keystores/` — provisioned Habery trees (shared `headDirPath`), reopened by both daemons via `--data-dir`; each daemon opens its **own `name`-distinguished Habery** under it (`base=""` for both — a distinct Habery = distinct LMDB environment, no shared-writer contention — see Decisions and the Phase 1c `--name`/`--alias` contract).
- `~/Library/Application Support/KERIGuard/sentinel/{sentinel_hab.pre}.sock` — relocated from `/tmp` via `sentinel start --socket-dir` (macOS only, Phase 1a; exercised via `setup_hk` in SaaS mode). Any in-process `LocalWatcherConnector` use must pass the same `socket_dir`.
- `~/Library/Application Support/KERIGuard/sentinel/config.yaml` — generated SaaS config, written by `sentinel_launch.py` before the plist.
- `~/Library/Application Support/KERIGuard/server.bran` — bran = daemon passcode file (read via `--passcode-file`), mode `0600` (Phase 1b).
- `~/Library/Application Support/KERIGuard/server.otp` — guardian witness OTP-secret, mode `0600`.
- `~/Library/Application Support/KERIGuard/guardian.heartbeat` — guardian liveness (Phase 1d).
- No new control/status socket for guardian (Phase 1d — heartbeat file instead).

---

## Phase 4 — CI/CD & PyInstaller integration

Depends on Phase 1 patches being **tagged** (for pinning) and landed (for the socket/passcode/heartbeat/`--data-dir` behavior). Do this pass together with Phase 2 (shared files).

- Add two new PyInstaller targets (`Analysis`/`EXE()` entries in `Locksmith.spec`) producing frozen `kg`/`sentinel` executables from their existing console-script entry points: `keriguard/pyproject.toml:70` (`kg = "keriguard.app.cli.kg:main"` — **not** the `kg-sentinel` entry at line 71) and `sentinel/pyproject.toml:52` (`sentinel = "sentinel.app.cli.sentinel:main"`). Embed at e.g. `Contents/Resources/kg-guardian` / `Contents/Resources/sentinel-daemon`.
- Extend `sign.sh`'s existing signing loop to cover these two new executables — no new signing/notarization pipeline needed.
- Extend the entitlements audit (Phase 2b) to the new daemons' file paths (`.../keystores/`, `.../sentinel/`, `server.bran`, heartbeat files) **plus the decided paths** (`server.otp` for the witnessed guardian, `sentinel/config.yaml` for the SaaS sentinel; the `--data-dir` keystore tree is the `.../keystores/` entry already listed).
- Pin `keriguard`'s `sentinel` dependency (`keriguard/pyproject.toml:30`: `sentinel @ git+https://git@github.com/healthKERI/sentinel@main`) to a tag once `sentinel` starts tagging releases (Phase 1).

---

## Open Risks
| # | Risk | Resolved in |
|---|------|-------------|
| 1 | ~~Three independent `/tmp/sentinel_{hab.pre}.sock` hardcodings must move in lockstep~~ **IMPLEMENTED** as a threaded `socket_dir` + `sentinel start --socket-dir` (`sentinel` `3b67423`); residual risk is that the flag defaults to `/tmp`, so every macOS caller (plist *and* in-process `LocalWatcherConnector`) must pass it | Phase 1a (code complete; verify via `setup_hk` in Phase 5) |
| 2 | ~~`keriguard-plugin` has no CI/tags~~ **IMPLEMENTED**: CI added, versions bumped to `0.1.0`, `release.ci.yml` pinned to `@v0.1.0` (tag not yet cut/pushed). `keriguard`'s `sentinel` dependency is still unpinned | Phase 2a (code complete; tag pending) / Phase 4 |
| 3 | `KERIGUARD_HELPER_VERSION` defaults to `"current"` if unset | Phase 2c |
| 4 | ~~Does the guardian AID need a witness?~~ **DECIDED: yes.** Provision via the OTP-based flow (`reserve_witness_for_server` → OOBI-load → `authenticate_witness` → `rotate_witness`); OTP-secret stored at `~/Library/Application Support/KERIGuard/server.otp` | Decisions (decided) → provisioning + Phase 3c `ProgramArguments` |
| 5 | ~~Neither daemon CLI has a custom-`headDirPath` flag~~ **DECIDED + IMPLEMENTED: `--data-dir` on both CLIs** (`sentinel` `3b67423`, `keriguard` `56ee181`, incl. `kg guardian up` + persisted into the generated guardian YAML). Haberies are distinguished by `--name`/`--alias` (already-existing flags), not `--base` (`provisioning.py` hardcodes `base=""` for both) | Decisions (decided) → Phase 1c (code complete) |
| 6 | Phase 1 branches are unmerged and **untagged**, so Phase 4 cannot pin yet; a review pass could still change the flag surface the Phase 3 plists depend on | fork+review → merge → tag (blocks Phase 4) |

---

## Verification checklist (Phase 5)

Run after Phase 4 is substantially in place — this is when the packaging build is re-verified (per the prerequisite gate).

### Upstream Phase 1 behavior at runtime (validates Phase 1)

Unit tests pass in both repos already; these check the parts only a real daemon run can prove:
- [ ] Branches merged to `main` and **tagged** in both `sentinel` and `keriguard` (unblocks Phase 4 pinning).
- [ ] With `--socket-dir` passed, the socket appears under `~/Library/Application Support/KERIGuard/sentinel/` and **nothing** is created in `/tmp` — verified through the `setup_hk` (SaaS) path, and `watcher add` / the plugin's `LocalWatcherConnector` connects to it.
- [ ] `--passcode-file ~/.../server.bran` opens the keystore with no passcode on the command line (`ps` shows no secret), and `--passcode` is absent from the plist.
- [ ] With `--data-dir`, both daemons reopen the **provisioned** Habery (correct `--name`/`--alias`) and do **not** silently fall back to a default/empty keystore.
- [ ] `guardian.heartbeat` mtime advances once per poll cycle on an idle daemon, and goes stale when the daemon is killed (`guardian_check.py` threshold is poll-interval-derived).
- [ ] `kg guardian up` writes `data_dir` into the generated YAML, and `kg guardian start --config <that yaml>` honours it.

### Packaging cleanup (validates Phase 2)
- [ ] `sign.sh` signs against the checked-in `entitlements.plist` (no drift possible).
- [ ] Real sandboxed smoke test with the new file entitlements in place: vault open → Setup end-to-end → `KERIGuardHelper.app` launch.
- [ ] CI **fails** (does not silently default) when `KERIGUARD_HELPER_VERSION` is unset.
- [ ] A tagged release installs cleanly via the pinned CI reference.

### Daemon lifecycle (validates Phase 3/Phase 4)
- [ ] Launch both daemons via `launchctl bootstrap`; `launchctl print gui/<uid>/<label>` shows them running **after Locksmith quits** and **after a reboot**.
- [ ] Kill each process; confirm `KeepAlive` restarts it.
- [ ] **Clean wipe before provisioning:** confirm the App-Support tree is fully cleared before the from-scratch witnessed provisioning — no leftover `witness=False` state (`~/Library/Application Support/KERIGuard/keystores/`, `server.bran`, `server.otp`, `sentinel/config.yaml`) that a daemon could silently reopen via `--data-dir` and pick up the wrong AID shape.
- [ ] Confirm both daemons reopen their respective provisioned Haberies via `--data-dir ~/Library/Application Support/KERIGuard/keystores` — no fallback to a default location, no "Habery not found" at start.
- [ ] Confirm each daemon opens its **own `name`-distinguished Habery** (distinct LMDB environment, both with `base=""`) under the shared `--data-dir`, with explicit `--name`/`--alias` matching what provisioning wrote — and that guardian and sentinel do **not** share one LMDB environment (no concurrent-writer contention).
- [ ] Confirm `sentinel_launch.py` generates `~/Library/Application Support/KERIGuard/sentinel/config.yaml` **before** the plist, and the daemon starts cleanly in SaaS mode from `--config` (no `--name`/`--alias`/`--local`).
- [ ] Confirm the guardian AID is provisioned **with a witness** (OTP flow completed; `server.otp` present, mode `0600`) and the daemon starts with the witnessed `ProgramArguments`.
- [ ] Exercise `sentinel watcher add` end-to-end against the relocated socket path (via the `setup_hk`/SaaS path).
- [ ] Confirm the guardian heartbeat file updates each poll cycle.

### End-to-end
- [ ] Issue a real interface/connection credential to the new (witnessed) guardian AID (`kg interface create` / `kg peers connect`); confirm the guardian daemon picks it up and writes/reloads the WireGuard config via the existing `KERIGuardHelper` IPC path — **entirely with Locksmith closed and the vault locked**.