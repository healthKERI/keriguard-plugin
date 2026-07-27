# Daemonized Sentinel (identity watching) + Guardian (WireGuard management) for `keriguard_user`

## Context

Today, `keriguard_user` (the Locksmith plugin at `keriguard-plugin/plugins/user`) only watches KELs and applies WireGuard credentials while Locksmith is running *and* the relevant vault is unlocked: `_start_polling`/`_start_watcher` spawn an `asyncio.Task` and an embedded `sentinel.core.witnessing.Watcher` from `on_vault_opened`, and `on_vault_closed` tears both down (`keriguard-plugin/plugins/user/src/keriguard_user/plugin.py:96-337`). The watcher AID used for KEL-watching is created *inside* the vault's own keystore (`vault.hby.makeHab(...)`, `setup/page.py:449-461`), and WireGuard interface/connection credentials are issued to **the vault's own primary AID** — i.e. the human's identity doubles as the machine/network-peer identity. Neither of these can survive the vault being locked, because both require the vault's own passcode-protected keys.

The goal is to make KEL-watching ("sentinel") and WireGuard config management ("guardian") run as persistent background daemons, independent of Locksmith's process lifetime and independent of whether the vault is open. This requires a machine identity that isn't the human vault's own AID, with its own headless-unlockable keystore.

The standalone `keriguard`/`sentinel` repos already solve most of this for Linux: `kg guardian start` and `sentinel start` are production-tested, fully-working long-running daemons (file-based KEL/TEL/credential polling, systemd units, a Unix-socket RPC for watcher registration) — they just assume a human-run CLI bootstrap (`kg guardian up`, `sentinel up`) and systemd for supervision. hkweb already has the exact machine-identity bootstrap primitive needed (`/server/authcodes` + `/account/teams/servers`, the "TeamServer" model), and `sentinel up` is a close, if imperfect, existing example of using it. The work here is mostly **packaging and glue**, not new daemon logic — reuse `kg guardian start`/`sentinel start` as-is, and build the missing macOS supervision/provisioning layer around them.

**Confirmed decisions driving this plan:**
1. Both the KEL-watcher role and the WireGuard-peer role move to a **new, dedicated, headless machine identity**, provisioned via the ESSR/team-server auth-code flow — not the vault's own AID. No migration path needed (no production users of the old model).
2. Landing the currently-unmerged `feat-keriguard-build` branch (PyInstaller packaging, plugin-aware bundling, `KERIGuardHelper.app` embedding) into `locksmith` main is an explicit **Phase 0** prerequisite, not an assumption.

---

## Phase 0 — Land `feat-keriguard-build`

`locksmith` main currently builds via `flet build macos`/`flet build windows` (`.github/workflows/release.ci.yml`) — no `Locksmith.spec`, no PyInstaller, no `KERIGuardHelper.app` embedding at all. The entire pipeline this plan depends on exists only on the unmerged local branch `feat-keriguard-build` (tip confirmed not an ancestor of `main`).

1. **Merge `feat-keriguard-build`.** Key files: `locksmith/Locksmith.spec` (PyInstaller spec; `copy_metadata("keriguard-user")`/`copy_metadata("keriguard-admin")` are conditional on `scripts/plugin_flavor.py`'s `is_plugin_installed()`, which reads the same `importlib.metadata.entry_points(group="locksmith.plugins")` the runtime `PluginManager` uses — verify this still resolves correctly for whatever package-name casing `keriguard-user`'s dist-info actually uses); `locksmith/scripts/fetch_keriguard_helper.py` (downloads a version-pinned `KERIGuardHelper.app.zip` from DO Spaces, embeds at `Contents/Resources/KERIGuardHelper.app`, restores executable bits lost on zip extraction); `locksmith/scripts/sign.sh` and `locksmith/.github/workflows/release.ci.yml`.
2. **Fix the unpinned plugin dependency.** `release.ci.yml`'s plugin-flavor build job installs `keriguard-user`/`keriguard-admin` via unpinned `git+https://github.com/healthKERI/keriguard-plugin.git@main`. `keriguard-plugin` has zero git tags and zero CI today. Establish semver tagging on `keriguard-plugin` (bump `plugins/user/pyproject.toml`/`plugins/admin/pyproject.toml`'s static `version = "0.0.1"` to match), pin the CI install to a tag, and add a minimal CI workflow (none exists) — at least an install + import smoke test.
3. **Fix the entitlements duplication + gap.** `locksmith/scripts/sign.sh` does **not** read the checked-in `locksmith/entitlements.plist` — it regenerates an independent copy via a heredoc (`sign.sh:101-126`) and signs against *that*. These two copies are currently identical but can silently drift; make `sign.sh` reference the checked-in file directly instead of regenerating it. Separately, `entitlements.plist` declares `app-sandbox=true` + `network.client`/`network.server` but **no `com.apple.security.files.*` entitlement**, despite `KERIGuardUserBaser`'s LMDB store, the user-chosen WireGuard config directory (`setup/page.py:333-340`), and `~/.locksmith/*.pem` all doing file I/O outside the sandbox container. This has never been exercised under a real sandboxed build. Add the needed entitlement (`files.user-selected.read-write` at minimum, likely a temporary-exception path for the LMDB/pem paths) and run an actual sandboxed smoke test (open a vault, run setup, launch the helper) before calling Phase 0 done — this must pass *before* the new daemons add more headless-file-I/O paths on top.
4. **Fix the `KERIGUARD_HELPER_VERSION` default.** `fetch_keriguard_helper.py` defaults to the string `"current"` if the env var is unset — an implicit-latest footgun for a security-sensitive embedded binary. Make it a required, non-defaulted CI input.

---

## Phase 1 — New machine-identity provisioning

**Status (2026-07-26):** `core/provisioning.py` and `core/keystore.py` exist, the setup-page step (1a/1b) is wired up, and the two-Habery split decided in 1d is now implemented — `bootstrap_server_identity()` opens two independent Haberies (`{name}`/guardian and `{name}-sentinel`/sentinel) and registers both via a single `connect_to_healthkeri()` call, matching `kg guardian up`'s proven shape exactly (`sentinel_hab` as the primary/authenticating identity, `server_hab` as the secondary/witnessed identity). `KERIGuardUserSettings` gained `sentinel_name`/`sentinel_alias`/`sentinel_aid` fields alongside the existing `server_*` fields, and `setup/page.py` persists both AIDs. Verified so far: `py_compile` + live import against `locksmith/venv` (including `sentinel.framework.connect_to_healthkeri`'s actual signature matching the call site), plus a local smoke test confirming the two Haberies produce genuinely distinct AIDs and that the sentinel identity's inception event cross-registers correctly into the guardian Habery's `kevers` (no network call). **Still not tested end-to-end against a live hkweb** — that remains the blocking item before Phase 1 can be considered complete; see Verification section.

### 1a. Obtaining the auth code (no new code)

Reuse `generate_auth_code()` exactly as-is: `locksmith/src/locksmith/ui/vault/healthKERI/core/remoting.py:2019-2037`, which does `POST /server/authcodes` (`hkweb/src/hkapi/app/api/team.py:168-217` → `TeamService.create_auth_code`, `hkweb/src/hksvc/core/services/team_service.py:581-613`) using the healthKERI plugin's own ESSR client (`vault.plugin_state["healthkeri"]["essr"]`). This requires the vault to already have a configured healthKERI account — surface an actionable error in the new setup UI if it doesn't, rather than failing silently.

**Implemented**: `setup/page.py`'s `_provision_server_identity()` checks `vault.plugin_state["healthkeri"]["essr"]` is configured (actionable error if not) before calling `generate_auth_code()`.

### 1b. New setup-page step

Added a "Server Identity" section + `_provision_server_identity()` to `keriguard-plugin/plugins/user/src/keriguard_user/setup/page.py`, wired into `_run_initialize()`, replacing the old in-vault watcher-hab creation. It calls `generate_auth_code(...)` then hands the plaintext code (in-memory only — never persisted, logged, or written to the settings DB) to `bootstrap_server_identity()` via `loop.run_in_executor`, and persists the resulting identity into settings (`KERIGuardUserSettings.server_name`/`server_alias`/`server_base_dir`/`server_aid`, additive alongside the existing `watcher_alias`).

### 1c. Bootstrap logic — `core/provisioning.py`

`bootstrap_server_identity(base_dir, bran, name, alias, auth_code, witness=False)` creates a non-delegated `habbing.Habery`/hab (`toad=0`) and registers it with healthKERI by calling `sentinel.framework.connecting.connect_to_healthkeri()` **directly** — no hand-built multipart POST needed. Model the call shape on `keriguard/.../guardian/up.py:230-238` (proven working — `await`ed, all 7 args supplied including `witness=True`), **not** `sentinel`'s own `up.py:188-190` (broken: missing `await`, missing the required `sentinel_hby` arg, never passes `server_hab`/`witness` — zero test coverage on this path, see Risk #10).

This is verified against `hkweb/src/hkapi/app/api/account.py`'s `TeamServerCollectionEnd.on_post` (lines 198-306): it accepts multipart parts `doc` (`aid` + `server_auth_code` required, `server_aid`/`delegated_aid` optional) and `kel` (required), plus an optional `server_kel` for a secondary identity — `connect_to_healthkeri()` builds exactly this shape.

- **Non-delegated by design**: delegating the server AID from the vault's own AID would require the vault to co-sign every future rotation, reintroducing "the daemon needs the vault open" for the identity meant to operate headlessly. hkweb also has no real human-approval gate today (`add_server_to_team` jumps straight `PENDING_REGISTRATION` → `LIVE`; `PENDING_APPROVAL` exists in the enum but nothing sets it) — treat approval-gate delegation as future hardening once hkweb implements one.
- **Witness support**: `connect_to_healthkeri`'s witness path (`reserve_witness_for_server` → OOBI-load → `authenticate_witness` → `rotate_witness`, an OTP-based flow: `authenticate_witness` decrypts a TOTP secret via `server_hab.decrypt` and persists it to `~/.keriguard/{server_hab.pre}` mode `0600`) reserves **exactly one** witness, and only runs when a secondary `server_hab`/`server_hby` pair is also passed with `witness=True`. There is no way to request N witnesses through this function — hkweb's endpoint does read a `doc.number_of_witnesses` field, but that path only takes effect with a `delegated_aid` set, which this design avoids. `bootstrap_server_identity()` currently defaults `witness=False` (`toad=0`, no witnesses) — see Risk #11 for the open decision on whether the daemon identity needs one, and note the `~/.keriguard/{aid}` OTP-secret path needs to land somewhere sane on macOS if it does.
- **`base` vs `headDirPath`**: `hio.base.filing.Filer.__init__` hard-rejects an absolute `base` (`FilerError`, `hio/base/filing.py:134`) — the keystore's absolute App-Support path must go in `headDirPath`, with `base=""`. This was a real defect found and fixed in `provisioning.py` (verified via a standalone Habery-creation smoke test) — it would have raised on the very first live call. This interacts with Risk #12: neither `sentinel start` nor `kg guardian start` exposes a `headDirPath`/custom-data-dir flag today, only a relative `--base`, so a daemon process can't yet reopen a Habery provisioned this way (see Phase 3a/5a).
- Run via `loop.run_in_executor(None, ...)` from the async setup handler, the same pattern `setup/page.py` already uses for other blocking KERI calls (e.g. `load_oobi`, line 429).
- `core/keystore.py` (new) provides `keystores_dir()` and `load_or_create_bran()`/`generate_bran()`, pulling forward the minimal parts of Phase 5a/5b needed to create/reopen a Habery at all — full Keychain storage and the upstream `--passcode-file` flags remain Phase 5b follow-up.
- **Not yet verified end-to-end against a live hkweb.** Signatures/shapes were verified by direct code reading plus `py_compile`/import checks against `locksmith/venv`, but no live registration has been run. Per the Verification section: dry-run against a local hkweb instance with a real auth code and confirm `POST /account/teams/servers` returns `201` and the server reaches `LIVE` via `GET /servers`, before relying on this in Phase 2/3.

### 1d. Two Haberies/two AIDs, not one shared identity

**Tested and rejected 2026-07-25.** The original design used a single machine identity for both sentinel and guardian roles, meaning `kg guardian start` and `sentinel start` (Phase 2, two separate OS processes) would open the *same* Habery/AID concurrently. `scripts/habery_concurrency_test.py` (real OS subprocesses, not threads) drove concurrent representative writes against a shared hab — `hab.interact()` for the sentinel role, `issuer.issue()`+`hab.interact()` (via `keri.vdr.credentialing.Regery`) for the guardian role, both anchoring into the same KEL, each iteration opening a fresh `Habery` (the realistic daemon-poll-loop pattern). Reproduced across three runs (40/25/60 iterations/role):

| iterations/role | writes ok/attempted (both roles) | expected final sn | actual final sn | lock/corruption errors |
|---|---|---|---|---|
| 40 | 40/40 | 81 | 44 | 0 |
| 25 | 25/25 | 51 | 27 | 0 |
| 60 | 60/60 | 121 | 62 | 0 |

**Verdict: every call succeeded with no exception and no LMDB corruption/lock error, yet ~half of all "successful" writes were silently lost.** Root cause (`habbing.py:1266-1289`): each `hab.interact()` computes `sn` from its own stale, freshly-opened view of the KEL; when the other process wins the race and commits first, the loser's now-stale-sn event is dropped by `Kevery` rather than raising back to the caller. This is a `Kevery`-level silent-drop-on-sn-race, not a raw-LMDB contention problem (`dbing.LMDBer` handles multi-process env access fine on its own).

**Decision**: reject the single-shared-Habery design. Use two independent Haberies/AIDs instead, matching the already-proven `kg guardian up` shape (`keriguard/.../guardian/up.py:130-155` — a `keriguard` Habery and a separate `{keriguard}-sentinel` Habery), each a genuinely distinct AID, registered together via one `connect_to_healthkeri()` call (`sentinel_hab`/`server_hab` as distinct AIDs, not the same hab passed twice).

**Implemented (2026-07-26)**: `provisioning.py`'s `bootstrap_server_identity()` now opens two independent Haberies via a shared `_open_or_create_hab()` helper — `{name}`/`{alias}` (guardian/WireGuard-peer role, becomes `connect_to_healthkeri`'s `server_hab`) and `{name}-sentinel`/`{alias}-sentinel` (KEL-watcher role, becomes `sentinel_hab`, the primary/authenticating identity) — cross-registers the sentinel's inception event into the guardian Habery's `kevers` (mirroring `kg guardian up`'s `parsing.Parser().parse(...)` + `Organizer.update(...)` step), then makes one `connect_to_healthkeri()` call registering both AIDs. Returns `server_aid` plus `sentinel_name`/`sentinel_alias`/`sentinel_aid`; `setup/page.py` and `KERIGuardUserSettings` persist all of it. Verified locally (Habery creation + cross-registration, no network) — **not yet** verified against a live hkweb registration call.

### 1e. Settings repointing — not started

`KERIGuardUserSettings.watcher_alias` (`keriguard-plugin/plugins/user/src/keriguard_user/db/basing.py:18`) currently names a hab inside `vault.hby`. The new `server_name`/`server_alias`/`server_base_dir`/`server_aid` fields exist on `KERIGuardUserSettings` (additive, not replacing `watcher_alias`) but nothing consumes them yet. Every consumer of the old field — `_start_watcher`, `_build_essr`'s AID source, and `CredService`'s `sentinel_aid` param (`core/applying.py:41-48`), all of which currently resolve `vault.hby.habByName(settings.watcher_alias)` — still needs to be repointed at the new dedicated Habery(-ies). Deliberately deferred: Phase 2 already decided `kg guardian start`/`sentinel start` become separate launchd-supervised OS processes reading exported CESR files + a socket, meaning `plugin.py`'s entire embedded `Watcher`/`CredentialPoller`/`WireGuardApplier` loop is slated for replacement (not repair) once Phase 3 lands — rewiring it to the new Habery now would likely be thrown away. It also depends on 1d's two-Habery redesign landing first.

---

## Phase 2 — One daemon or two

**Recommendation: keep guardian and sentinel as two separate long-running processes**, running (macOS-adapted) `kg guardian start` and `sentinel start` independently, mirroring the already-proven Linux split (`keriguard/debian/keriguard-guardian.service` / `keriguard-sentinel.service`, separate systemd units with independent restart policies). Reasons: this reuses tested code paths in both CLIs unchanged; failure domains stay independent (a WireGuard-apply bug in guardian doesn't take down KEL-watching); and the two already coordinate only through the on-disk exported CESR files (guardian reads `export_dir/{kel,tel,credential}/*.cesr`, `sentinel/src/sentinel/framework/watching.py:76-127`) and a Unix socket (Phase 4a) — a boundary that already fully decouples them. Merging into one process would mean rewriting/merging two independently-evolving event loops (`sentinel.framework.run()` vs. `sentinel`'s own service loop in `start.py:220-323`) for no clear benefit here.

---

## Phase 3 — macOS packaging & lifecycle

### 3a. New files (in `keriguard-plugin`, not a new repo, not vendored into `keriguard`/`sentinel`)

Direct templates already exist for this exact pattern — `core/helper_launch.py` and `core/helper_check.py` (how Locksmith launches/health-checks `KERIGuardHelper.app` via `subprocess.Popen(["open","-a",...])` + `launchctl print gui/<uid>/<label>`). Add, in `keriguard-plugin/plugins/user/src/keriguard_user/core/`:
- `sentinel_launch.py` / `guardian_launch.py` — install + `launchctl bootstrap gui/$(id -u) <plist>` the two daemons.
- `sentinel_check.py` / `guardian_check.py` — health checks (Phase 4b).
- Bundled launchd plist templates under a new `keriguard-plugin/plugins/user/resources/launchd/` (e.g. `com.healthkeri.keriguard.sentinel.plist`, `com.healthkeri.keriguard.guardian.plist`), analogous to `keriguard-helper/Sources/KERIGuardHelper/Resources/com.healthkeri.keriguard.helper.agent.plist`.

Note: this depends on resolving Risk #12 (custom `headDirPath` vs. `--base`-only CLI flags) — see Phase 1c and 5a.

### 3b. Plain frozen CLI binaries, not signed `.app` bundles

`KERIGuardHelper.app` needs the full Xcode/System-Extension/notarization pipeline because it *owns the actual WireGuard tunnel* via `NEPacketTunnelProvider`. `kg guardian start` never touches the tunnel directly — it already delegates all tunnel control to KERIGuardHelper's existing Unix-socket IPC (`keriguard/src/keriguard/core/systeming.py:158-211`, same socket/protocol as `helper_check.py`). So the new daemons have **no System-Extension entitlement need at all**: freeze them with PyInstaller and sign them with the same `codesign` loop `scripts/sign.sh` already runs over other executables (`sign.sh:141-150`) — no new Xcode pipeline, no new notarization pipeline (they ride inside the already-notarized outer `.app`/DMG).

### 3c. Supervision: bundled launchd agents, not `SMAppService`

`SMAppService.agent(plistName:)` is Swift/`ServiceManagement`-only — no Python binding, and it requires a `.app` bundle identity these daemons don't have. Instead: ship the plist templates as bundled resources; at first successful provisioning (Phase 1), `sentinel_launch.py`/`guardian_launch.py` write the filled-in plist (executable path, `--name`/`--base`/`--passcode-file` per Phase 5) to `~/Library/LaunchAgents/com.healthkeri.keriguard.{sentinel,guardian}.plist` and run `launchctl bootstrap gui/$(id -u) <path>`. `RunAtLoad=true` + `KeepAlive` gives crash-restart behavior matching the Debian units' `Restart=on-failure`. `contrib/systemd/sentinel@.service` (a real `%i`-templated systemd unit, `ExecStart=/usr/local/bin/sentinel start --config /etc/sentinel/%i.conf`, `Restart=on-failure`) is a good template to crib the plist from.

`ProgramArguments` mirror the existing proven CLI invocations from `keriguard/debian/keriguard-guardian.service` / the `sentinel@.service` template — `kg guardian start --sentinel-aid <server_hab.pre> --sentinel-export-dir <export_dir> ...` and, for **local** mode, `sentinel start --name <server_name> --alias <server_alias> --base <server_base_dir> --local --uxd --export-dir <export_dir>` (mapping the plugin's existing `registrar`/`healthKERI` credential-source setting to `--local` vs. a SaaS config). **Correction**: `sentinel start` now hard-requires `--config <yaml>` in SaaS mode (`--local` unset) — `merge_config_and_args()`/`SentinelConfig` in `start.py` raises `ValueError` without it, and `server_name`/`server_alias` can no longer be passed as flags in that mode at all. If this daemon ever runs in healthKERI SaaS mode rather than local witness-querying mode, the launchd plist must point `--config` at a generated YAML file instead of flags. Confirmed: `sentinel start` still has no `--passcode-file` option (Phase 5b's gap, only `--passcode`/`-p`).

---

## Phase 4 — IPC design

### 4a. Sentinel watcher-add — reuse as-is, relocate off `/tmp`

`sentinel watcher add` already talks to a running `sentinel start --uxd` process via `LocalWatcherConnector` over a Unix socket (`sentinel/src/sentinel/framework/watching.py:227-308`). Verified: both client and server independently hardcode `f"/tmp/sentinel_{watcher}.sock"` — client at `watching.py:250`, server at `sentinel/src/sentinel/app/sentineling.py:106` (`setup_local`) and `:186` (`setup_hk`). These three sites must move together for macOS packaging (small, contained patch — thread a `socket_dir` parameter through, defaulting to `/tmp` for existing Linux/Debian packaging, overridden to `~/Library/Application Support/KERIGuard/sentinel/` for the macOS launchd agent, matching the existing `~/Library/Application Support/KERIGuard/helper.sock` convention). Have the plugin call `LocalWatcherConnector` directly (or a thin wrapper) rather than shelling out to `sentinel watcher add`.

### 4b. Guardian health — reuse `launchctl print` + a heartbeat file, no new socket

Guardian has no local control/status socket on any platform today (Linux uses systemd D-Bus, `systeming.py:100-155`, which has no macOS analog). Adding a new status socket would mean modifying `sentinel.framework.runner.run()` itself (`sentinel/src/sentinel/framework/runner.py:19-158`) to run a second listener alongside its own event loop — new, unproven code in the exact "reuse production-tested logic" path this plan is trying to preserve. Recommended instead: `guardian_check.py` uses `launchctl print gui/$(id -u)/com.healthkeri.keriguard.guardian.agent` for "is it running" (identical to `helper_check.py:is_helper_installed`), plus a small addition to `keriguard/src/keriguard/app/sentinel/handler.py`'s `KeriguardEventHandler` — touch a heartbeat file (e.g. `~/Library/Application Support/KERIGuard/guardian.heartbeat`) once per successful poll cycle — for staleness detection. Cheaper than new IPC surface, and avoids adding another unauthenticated same-UID socket.

### 4c. Resulting socket inventory

- `~/Library/Application Support/KERIGuard/helper.sock` — existing, unchanged.
- `~/Library/Application Support/KERIGuard/sentinel/{server_hab.pre}.sock` — relocated from `/tmp` (macOS only).
- No new socket for guardian.

---

## Phase 5 — Keystore/secret storage

### 5a. Location

`habbing.Habery.__init__` signature (confirmed): `(self, *, name='test', base='', temp=False, ks=None, db=None, cf=None, clear=False, headDirPath=None, **kwa)` — `base`/`headDirPath` fully support a custom location. Use `headDirPath = ~/Library/Application Support/KERIGuard/keystores`, `base = ""`, `name = f"{vault.hby.name}-server"` — a **sibling** to the human vault's own LMDB tree, not nested inside it, so deleting the Locksmith vault doesn't orphan/break the still-running daemon (and vice versa). This is a real behavior change worth being explicit about: the machine identity outlives the vault that provisioned it.

**Open conflict with Phase 3 (Risk #12)**: neither `sentinel start` (`start.py:233,243`) nor `kg guardian start` (`guardian/up.py:134,145`) exposes a `headDirPath`/custom-data-dir flag — only a relative `--base`, matching keripy's default `headDirPath` (`/usr/local/var`, falling back to `~`). A Habery provisioned under this sandbox-friendly absolute path (needed because Locksmith.app runs under `app-sandbox=true`, Phase 0.3) **cannot be reopened by an unmodified `sentinel start --base <server_base_dir>` / `kg guardian start --base <server_base_dir>` daemon** — there's no flag for it. Unresolved: either patch both upstream CLIs to accept a `headDirPath`/`--data-dir` override (cuts against "no CLI patches"), or provision under the default/fallback location instead (dropping the App-Support isolation this section wants). Needs a decision before Phase 3 can work.

### 5b. Bran storage — file, not Keychain, for v1

Neither `kg guardian start` nor `sentinel start` has any Keychain-reading code path today; both take `--passcode`/`-p` as a plain string. Building Keychain integration means patching both upstream CLIs' passcode-acquisition logic — cuts against "reuse production-tested code." Recommended: store the bran in `~/Library/Application Support/KERIGuard/server.bran` (mode `0600`, owner-only), and add a small `--passcode-file` option to both `kg guardian start` (`keriguard/src/keriguard/app/cli/commands/guardian/start.py`) and `sentinel start` (`sentinel/src/sentinel/app/cli/commands/start.py`) that reads the file instead of taking the secret on argv (visible via `ps`). This is the same trust boundary the helper's own socket already uses (same-UID file permissions, no auth token, per `IPCServer.swift`'s documented design) — consistent with existing precedent, not a new weaker link. Generate the bran with `keri.app.keeping.Algos.randy` (same as `sentinel up.py:107-109`'s no-`--salt` path), independent of the human vault's own passcode. Flag Keychain as a good v2 hardening step (survives stolen-disk scenarios a plain file doesn't) once the upstream CLIs support it.

---

## Phase 6 — CI/CD implications

- Add two new PyInstaller targets (or `Analysis`/`EXE()` entries in `Locksmith.spec`) producing frozen `kg`/`sentinel` executables from their existing console-script entry points (confirmed: `keriguard/pyproject.toml:70`, `kg = "keriguard.app.cli.kg:main"`; `sentinel/pyproject.toml`, `sentinel = "sentinel.app.cli.sentinel:main"`), embedded at e.g. `Contents/Resources/kg-guardian` / `Contents/Resources/sentinel-daemon`.
- Extend `scripts/sign.sh`'s existing signing loop (lines 141-150) to cover these two new executables — no new signing/notarization pipeline, they ride inside the already-notarized outer bundle. This is materially cheaper than replicating `keriguard-helper`'s Xcode/System-Extension CI — call this out explicitly when scoping, since it's a meaningful cost reduction.
- Extend the Phase 0 entitlements audit to the new daemons' file paths (`.../keystores/`, `.../sentinel/`, `server.bran`, heartbeat files).
- **Verified, pre-existing version-drift bug, independent of this project but blocking anything that leans on `keriguard`'s SaaS bootstrap path**: at one point, `keriguard/src/keriguard/app/cli/commands/guardian/up.py:20`'s `from sentinel.framework.connecting import connect_to_healthkeri` did not resolve against a stale local `sentinel` checkout (no `connecting.py` existed yet). This has since been resolved upstream (see Phase 1c), but the underlying risk — `keriguard`'s `sentinel` dependency is unpinned (`keriguard/pyproject.toml:30`, `sentinel @ git+...@main`) — remains a concrete argument for pinning it to a tag once `sentinel` starts tagging releases.
- Fix the `KERIGUARD_HELPER_VERSION` default (Phase 0.4).

---

## Risks / Open Items

| # | Risk | Status |
|---|------|--------|
| 1 | `sentinel up.py`'s multipart call to `/account/teams/servers` vs. the endpoint's actual accepted shape | **Resolved** — `up.py` rewritten upstream to drop the delegated-hab design; current shape matches `hkweb`. See Phase 1c. |
| 2 | hkweb never enforces an admin-approval gate before a server goes `LIVE`; `/server/authcodes` and `/account/teams/servers` have no rate limiting | Out of scope (hkweb-side); flag to hkweb owners |
| 3 | `keriguard`'s SaaS-mode `guardian up` importing `connect_to_healthkeri` | **Resolved** — `sentinel/src/sentinel/framework/connecting.py` exists and `guardian/up.py:230-238` calls it correctly. See Phase 1c. |
| 4 | Sandbox entitlements gap — no `files.*` entitlement despite real file I/O | Open — Phase 0.3, needs an actual sandboxed-build smoke test |
| 5 | Delegating the new server AID from the vault's AID would reintroduce "daemon needs vault open" for rotations | Avoided by design — Phase 1c |
| 6 | Three independent `/tmp/sentinel_{aid}.sock` hardcodings must move in lockstep | Open — Phase 4a, small contained patch, all three sites |
| 7 | Concurrent two-process access to one shared Habery/LMDB env | **Resolved — tested, FAILED; two-Habery split now implemented.** Silent write loss under concurrent same-AID writers with zero raised exceptions. Single-shared-Habery design rejected; `provisioning.py` now provisions two independent AIDs. See Phase 1d. |
| 8 | `keriguard-plugin` has no CI/tags; `keriguard`'s `sentinel` dependency is unpinned | Open — Phase 0.2 / Phase 6 |
| 9 | `KERIGUARD_HELPER_VERSION` defaults to `"current"` if unset | Open — Phase 0.4 |
| 10 | `sentinel`'s own `up.py:188-190` calls `connect_to_healthkeri` incorrectly (missing `await`, missing required `sentinel_hby` arg, never passes `server_hab`/`witness`); zero test coverage on this path | Not blocking (Phase 1c copies `guardian/up.py`'s call shape instead) — worth flagging to `sentinel` maintainer |
| 11 | Witness provisioning for a server AID is a separate OTP-based flow (`reserve_witness_for_server` → OOBI-load → `authenticate_witness` → `rotate_witness`), not a `number_of_witnesses` field on the registration `doc` | Open design question: does the new machine identity need a witness at all (current draft uses `toad=0`); if yes, reuse this flow — see Phase 1c |
| 12 | Neither daemon CLI has a flag for a custom `headDirPath` (only relative `--base`), but the sandboxed Locksmith.app needs an absolute, sandbox-friendly `headDirPath` to provision the Habery at all | Open — blocks Phase 3 as scoped ("reuse CLIs as-is"); needs a reconciliation decision before Phase 3. See Phase 1c/5a. |

---

## Verification

- **Phase 0**: build `Locksmith.app` from the merged branch with entitlements fixed and `app-sandbox=true` actually enforced; run through vault-open → setup → helper-launch manually and confirm no sandbox file-access denials in Console.app.
- **Phase 1**: dry-run `bootstrap_server_identity()` against a local hkweb instance (per `scripts/dev_env.py`'s existing local dev flow) with a real auth code; confirm `POST /account/teams/servers` returns `201` and the server's `status` reaches `LIVE` via `GET /servers`. Still outstanding as of 2026-07-25.
- **Phase 1d concurrency test**: done, failed — see Phase 1d for full detail. Single-Habery design rejected; two-Habery split required before Phase 1 can be considered complete.
- **Phase 3/4**: launch both daemons via `launchctl bootstrap`, confirm `launchctl print gui/<uid>/<label>` shows them running after Locksmith quits and after a reboot; kill each process and confirm `KeepAlive` restarts it; exercise `sentinel watcher add` end-to-end against the relocated socket path; confirm the guardian heartbeat file updates each poll cycle.
- **End-to-end**: issue a real interface/connection credential to the new server AID (via `kg interface create` / `kg peers connect` against the new AID) and confirm the guardian daemon picks it up and writes/reloads the WireGuard config via the existing KERIGuardHelper IPC path, entirely with Locksmith closed and the vault locked.