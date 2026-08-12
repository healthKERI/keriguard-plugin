#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""Phase 1d concurrency test -- PLAN.md "One shared identity for both sentinel
and guardian roles (verify concurrency before committing)".

Opens the SAME `keri.app.habbing.Habery` (same name/base/bran, i.e. the same
on-disk LMDB keystore) from two independent OS processes and drives writes
representative of each role for the whole duration each process runs:

- "sentinel" role: repeated `hab.interact()` calls -- a cheap, repeatable
  stand-in for KEL/witness-receipt state advancement.
- "guardian" role: repeated credential-registry TEL writes via
  `keri.vdr.credentialing.Regery` (registry inception once, then repeated
  `issuer.issue()` + `hab.interact()` anchor per credential) -- the same
  call shape as `keripy/tests/vdr/test_issuing.py`, representative of
  WireGuard credential-apply state advancement.

Both roles anchor into the *same* hab's KEL, so this deliberately exercises
the actual contention point: two independently-opened Habery objects, in two
processes, both racing to advance one AID's sequence number, not just raw
LMDB env access.

Usage:
    # Orchestrator (spawns both worker subprocesses, waits, verifies):
    python3 scripts/habery_concurrency_test.py [--iterations N] [--keep]

    # Worker mode (invoked internally as a subprocess; not for direct use):
    python3 scripts/habery_concurrency_test.py --worker sentinel|guardian \\
        --base-dir DIR --bran BRAN --name NAME --alias ALIAS \\
        --iterations N --out PATH
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _bootstrap_identity(base_dir: str, bran: str, name: str, alias: str) -> str:
    from keri.app import habbing

    hby = habbing.Habery(name=name, base="", headDirPath=base_dir, bran=bran, temp=False)
    try:
        hab = hby.habByName(alias)
        if hab is None:
            hab = hby.makeHab(
                name=alias,
                transferable=True,
                icount=1,
                isith="1",
                ncount=1,
                nsith="1",
                toad=0,
            )
        return hab.pre
    finally:
        hby.close()


def run_sentinel_worker(base_dir: str, bran: str, name: str, alias: str, iterations: int, out_path: str) -> None:
    from keri.app import habbing

    results = []
    for i in range(iterations):
        hby = habbing.Habery(name=name, base="", headDirPath=base_dir, bran=bran, temp=False)
        try:
            hab = hby.habByName(alias)
            hab.interact(data=[{"seq": i, "role": "sentinel", "kind": "witness-update"}])
            results.append({"i": i, "ok": True, "sn": hab.kever.sn})
        except Exception as exc:
            results.append({"i": i, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            hby.close()
        time.sleep(random.uniform(0, 0.03))
    Path(out_path).write_text(json.dumps(results))


def _make_regery(hby, base_dir: str, reg_name: str):
    """`credentialing.Regery.__init__` builds its own `Reger` WITHOUT
    forwarding `headDirPath` -- it always lands at keripy's shared default
    location (`/usr/local/var/keri/reg/...`, falling back to `~/keri/reg/...`),
    never at the isolated `base_dir` this test uses for the Habery itself.
    Confirmed by observation: an earlier version of this script that called
    `credentialing.Regery(hby=hby, name=reg_name, temp=False)` directly wrote
    a real, persistent `server-reg` directory into this machine's actual
    `/usr/local/var/keri/reg/` (alongside real dev-environment witness/API
    keystores) and leaked state across separate test runs. Build the `Reger`
    explicitly with `headDirPath=base_dir` and hand it in via `reger=` to
    keep this test's writes confined to its own temp dir.
    """
    from keri.vdr import viring, credentialing

    reger = viring.Reger(name=reg_name, base="", db=hby.db, temp=False, reopen=True, headDirPath=base_dir)
    return credentialing.Regery(hby=hby, name=reg_name, reger=reger, temp=False)


def run_guardian_worker(base_dir: str, bran: str, name: str, alias: str, iterations: int, out_path: str) -> None:
    from keri.app import habbing
    from keri.core import coring
    from keri.core.eventing import SealEvent

    reg_name = f"{alias}-reg"
    results = []

    hby = habbing.Habery(name=name, base="", headDirPath=base_dir, bran=bran, temp=False)
    try:
        hab = hby.habByName(alias)
        regery = _make_regery(hby, base_dir, reg_name)
        if regery.regs:
            regk = next(iter(regery.regs))
            results.append({"i": "setup", "ok": True, "regk": regk, "note": "reused existing registry"})
        else:
            try:
                issuer = regery.makeRegistry(name=reg_name, prefix=hab.pre, noBackers=True)
                rseal = SealEvent(issuer.regk, "0", issuer.regd)._asdict()
                hab.interact(data=[rseal])
                seqner = coring.Seqner(sn=hab.kever.sn)
                issuer.anchorMsg(
                    pre=issuer.regk, regd=issuer.regd, seqner=seqner,
                    saider=coring.Saider(qb64=hab.kever.serder.said),
                )
                regery.processEscrows()
                regk = issuer.regk
                results.append({"i": "setup", "ok": True, "regk": regk})
            except Exception as exc:
                regk = None
                results.append({"i": "setup", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        hby.close()

    if regk is not None:
        for i in range(iterations):
            hby = habbing.Habery(name=name, base="", headDirPath=base_dir, bran=bran, temp=False)
            try:
                hab = hby.habByName(alias)
                regery = _make_regery(hby, base_dir, reg_name)
                issuer = regery.regs[regk]
                said = coring.Diger(ser=f"cred-guardian-{i}-{random.random()}".encode()).qb64
                iss = issuer.issue(said=said)
                rseal = SealEvent(iss.pre, "0", iss.said)._asdict()
                hab.interact(data=[rseal])
                results.append({"i": i, "ok": True, "sn": hab.kever.sn})
            except Exception as exc:
                results.append({"i": i, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            finally:
                hby.close()
            time.sleep(random.uniform(0, 0.03))

    Path(out_path).write_text(json.dumps(results))


# Hard "real damage" signals vs. expected sn-race exceptions -- surfaced
# separately in the report so a wall of ValidationErrors doesn't get
# mistaken for corruption, and vice versa.
_CORRUPTION_MARKERS = (
    "CorruptedError", "PanicError", "MapFullError", "ReadersFullError", "LockError",
)


def _summarize(role: str, results: list[dict]) -> dict:
    attempts = [r for r in results if r["i"] != "setup"]
    ok = [r for r in attempts if r["ok"]]
    failed = [r for r in attempts if not r["ok"]]
    corruption = [r for r in failed if any(m in r["error"] for m in _CORRUPTION_MARKERS)]
    setup = next((r for r in results if r["i"] == "setup"), {"ok": True})
    return {
        "role": role,
        "setup_ok": setup["ok"],
        "attempted": len(attempts),
        "succeeded": len(ok),
        "failed": len(failed),
        "corruption_signals": len(corruption),
        "error_types": sorted({r["error"].split(":")[0] for r in failed}),
    }


def orchestrate(iterations: int, keep: bool) -> int:
    from keri.app import habbing
    from keri.core import coring

    tmp_root = tempfile.mkdtemp(prefix="kg-concurrency-")
    base_dir = tmp_root
    name = "concurtest"
    alias = "server"
    bran = coring.randomNonce()[2:23]
    reg_name = f"{alias}-reg"

    print(f"[orchestrator] keystore dir: {base_dir}")
    aid = _bootstrap_identity(base_dir, bran, name, alias)
    print(f"[orchestrator] bootstrapped shared AID: {aid}")

    sentinel_out = str(Path(tmp_root) / "sentinel.json")
    guardian_out = str(Path(tmp_root) / "guardian.json")

    def spawn(role: str, out_path: str) -> subprocess.Popen:
        return subprocess.Popen([
            sys.executable, __file__, "--worker", role,
            "--base-dir", base_dir, "--bran", bran, "--name", name, "--alias", alias,
            "--iterations", str(iterations), "--out", out_path,
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    print(f"[orchestrator] launching sentinel + guardian workers, {iterations} iterations each ...")
    t0 = time.monotonic()
    p_sentinel = spawn("sentinel", sentinel_out)
    p_guardian = spawn("guardian", guardian_out)

    out_sentinel, _ = p_sentinel.communicate()
    out_guardian, _ = p_guardian.communicate()
    elapsed = time.monotonic() - t0
    print(f"[orchestrator] both workers exited after {elapsed:.1f}s "
          f"(sentinel rc={p_sentinel.returncode}, guardian rc={p_guardian.returncode})")

    if p_sentinel.returncode != 0:
        print("[orchestrator] sentinel worker stderr/stdout:\n" + out_sentinel)
    if p_guardian.returncode != 0:
        print("[orchestrator] guardian worker stderr/stdout:\n" + out_guardian)

    sentinel_results = json.loads(Path(sentinel_out).read_text())
    guardian_results = json.loads(Path(guardian_out).read_text())

    sentinel_summary = _summarize("sentinel", sentinel_results)
    guardian_summary = _summarize("guardian", guardian_results)

    expected_advances = (
        sentinel_summary["succeeded"]
        + (1 if guardian_summary["setup_ok"] else 0)
        + guardian_summary["succeeded"]
    )

    verify_error = None
    actual_sn = None
    registry_reload_ok = None
    try:
        hby = habbing.Habery(name=name, base="", headDirPath=base_dir, bran=bran, temp=False)
        try:
            hab = hby.habByName(alias)
            actual_sn = hab.kever.sn
            regery = _make_regery(hby, base_dir, reg_name)
            registry_reload_ok = bool(regery.regs)
        finally:
            hby.close()
    except Exception as exc:
        verify_error = f"{type(exc).__name__}: {exc}"

    report = {
        "iterations_per_role": iterations,
        "elapsed_s": round(elapsed, 1),
        "sentinel": sentinel_summary,
        "guardian": guardian_summary,
        "expected_final_sn": expected_advances,
        "actual_final_sn": actual_sn,
        "sn_matches": actual_sn == expected_advances,
        "registry_reload_ok": registry_reload_ok,
        "final_reopen_error": verify_error,
        "hard_corruption_signals": sentinel_summary["corruption_signals"] + guardian_summary["corruption_signals"],
    }

    print("\n=== Phase 1d concurrency test report ===")
    print(json.dumps(report, indent=2))

    verdict_lines = []
    if report["hard_corruption_signals"]:
        verdict_lines.append("FAIL: hard LMDB corruption/lock signals observed -- see error_types above.")
    if verify_error:
        verdict_lines.append(f"FAIL: fresh re-open/verify of the shared Habery raised: {verify_error}")
    if actual_sn is not None and not report["sn_matches"]:
        verdict_lines.append(
            f"FAIL: final sn ({actual_sn}) != successful-write count ({expected_advances}) -- "
            "possible silent lost write or double-count."
        )
    if not verdict_lines:
        verdict_lines.append(
            "PASS (no corruption, KEL self-consistent, sn matches successful-write count). "
            f"NOTE: {sentinel_summary['failed']} sentinel + {guardian_summary['failed']} guardian "
            "writes lost their sn race and raised expected ValidationError-class exceptions -- "
            "concurrent same-AID KEL advancement from two independent processes is NOT safe "
            "without external serialization between the two roles."
        )
    print("\n".join(verdict_lines))

    if keep:
        print(f"[orchestrator] keystore left at {tmp_root} (--keep)")
    else:
        shutil.rmtree(tmp_root, ignore_errors=True)

    _cleanup_stray_configer_file(name)

    return 1 if any(v.startswith("FAIL") for v in verdict_lines) else 0


def _cleanup_stray_configer_file(name: str) -> None:
    """`habbing.Habery`'s `configing.Configer` (`.cf`) never accepts
    `headDirPath` (see `habbing.py:213-215`) -- unlike `.ks`/`.db`, it always
    writes to keripy's shared default config location regardless of what
    this script passes for the rest of the keystore. Confirmed by
    observation: this test previously left `/usr/local/var/keri/cf/
    concurtest.json` behind on a real dev machine. Not test data worth
    keeping either way -- always remove it so repeat runs don't accumulate
    stray files in a directory shared with real dev-environment identities.
    """
    import glob

    for candidate in (
        f"/usr/local/var/keri/cf/{name}.json",
        str(Path.home() / f"keri/cf/{name}.json"),
    ):
        for path in glob.glob(candidate):
            try:
                Path(path).unlink()
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", choices=["sentinel", "guardian"])
    ap.add_argument("--base-dir")
    ap.add_argument("--bran")
    ap.add_argument("--name")
    ap.add_argument("--alias")
    ap.add_argument("--iterations", type=int, default=40)
    ap.add_argument("--out")
    ap.add_argument("--keep", action="store_true", help="keep the temp keystore dir for inspection")
    args = ap.parse_args()

    if args.worker == "sentinel":
        run_sentinel_worker(args.base_dir, args.bran, args.name, args.alias, args.iterations, args.out)
        return 0
    if args.worker == "guardian":
        run_guardian_worker(args.base_dir, args.bran, args.name, args.alias, args.iterations, args.out)
        return 0

    return orchestrate(args.iterations, args.keep)


if __name__ == "__main__":
    sys.exit(main())