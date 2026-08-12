# -*- encoding: utf-8 -*-
"""keriguard_user.core.daemon_app_info — static metadata describing the
minimal .app bundles the frozen kg-guardian / sentinel-daemon executables
are wrapped in (DAEMONS.md Phase 4).

Deliberately has NO other keriguard_user/keri imports: `locksmith`'s
`scripts/embed_daemons.py` loads this module directly (via
`importlib.util.find_spec`/`exec_module`) from a plain CI build venv to read
`DAEMON_APP_INFO`, outside of the frozen-app libsodium bootstrap that
`keri`/`pysodium` needs (`keri` dlopen()s libsodium as an import side
effect -- see `locksmith/src/locksmith/main.py`'s `load_custom_libsodium`).
Keeping this leaf module import-free of `keri` lets that script read the
dict without pulling `pysodium` in and crashing with "Unable to find
libsodium" on a runner/venv that has no system libsodium installed.
"""
from __future__ import annotations

# The daemon executables are embedded wrapped in a minimal .app bundle
# (Contents/MacOS/<executable>, Contents/Info.plist) rather than as bare
# Mach-O binaries directly under Contents/Resources. A bare signed
# executable has no CFBundleName/CFBundleDisplayName for macOS's Background
# Items / Login Items UI to show, so it falls back to the code signature's
# Common Name -- the developer's own name on the Developer ID Application
# cert -- as the displayed name. Wrapping in a bundle with its own
# CFBundleName fixes that. `locksmith/scripts/embed_daemons.py` builds these
# bundles at package time using this same mapping.
DAEMON_APP_INFO = {
    "kg-guardian": {
        "app_name": "KERIGuardGuardian.app",
        "bundle_name": "KERIGuard Guardian",
        "bundle_id": "com.healthkeri.keriguard.guardian",
    },
    "sentinel-daemon": {
        "app_name": "KERIGuardSentinel.app",
        "bundle_name": "KERIGuard Sentinel",
        "bundle_id": "com.healthkeri.keriguard.sentinel",
    },
}