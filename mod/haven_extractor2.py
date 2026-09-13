"""Haven Extractor 2.0 — entry mod (haven-ui/docs/EXTRACTOR_2_0.md).

The final pymhf Mod class. Capture organs are verbatim transplants living in
mixins (see capture/, resolve/, language/, payload/); this file owns:

  * the DECORATED hook methods (pymhf's mod_loader iterates the class __dict__,
    so decorators must live here — thin wrappers delegate to the *_impl bodies)
  * the 2.0 systems: telemetry event bus, readiness gate, local API, staging
    sync (queue + on-disk journal — a crash no longer loses the batch),
    heartbeat, command polling, pairing
  * revived gamemode/reality detection (dead code in 1.x — every submission
    said "Normal"; D13 folds it into natural capture)

No GUI fields, no export button, no username entry: the website is the
control surface. The env file next to the install root holds the key.
"""

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pymhf import Mod
import nmspy.data.types as nms
from nmspy.decorators import on_state_change, on_fully_booted

from capture.hooks_mixin import CaptureHooksMixin
from capture.systemread_mixin import SystemReadMixin
from capture.memory_mixin import MemoryMixin
from capture.namegen_probe import NMS_NAMEGEN_AVAILABLE
from language.language_mixin import LanguageMixin
from payload.payload_mixin import PayloadMixin
from resolve.resolve_mixin import ResolveMixin

from state import ExtractorState
from nmspy_pin import framework_status
from readiness import registry_names, unbound_required_hooks
from payload.sanity import payload_sanity_problems, payload_soft_warnings
from telemetry.events import EventBus
from config.env_file import load_env, save_env
from sync.client import HavenSyncClient, SyncError
from api_local.server import LocalApi

logger = logging.getLogger("haven_extractor2")

__version__ = "2.1.0-dev"

# Required memory hooks — the readiness gate checks these against pyMHF's
# registry after load (June 16 2026: 'Unable to find offset for
# cGcSolarSystem.Generate' and the mod still said "ready" — never again).
REQUIRED_HOOKS = (
    "cTkLanguageManagerBase.Translate",
    "cGcSolarSystem.Generate",
    "cGcPlanetGenerator.GenerateCreatureRoles",
)

ENV_FILENAME = "haven.env"


class HavenExtractor2(Mod, CaptureHooksMixin, SystemReadMixin, MemoryMixin,
                      LanguageMixin, PayloadMixin, ResolveMixin):
    __author__ = "Voyagers Haven"
    __version__ = __version__
    __description__ = "Haven Extractor 2.0 — web-controlled capture agent (havenmap.online/extractor)"

    # Fallback level maps (verbatim v1 class attrs; used by planet capture)
    FLORA_LEVELS = {0: "None", 1: "Sparse", 2: "Average", 3: "Bountiful"}
    FAUNA_LEVELS = {0: "None", 1: "Sparse", 2: "Regular", 3: "Copious"}
    SENTINEL_LEVELS = {0: "Minimal", 1: "Limited", 2: "High", 3: "Aggressive"}

    # ------------------------------------------------------------------ init

    def __init__(self):
        super().__init__()

        # ---- capture state buckets (verbatim v1 semantics) ----------------
        self._captured_planets = {}
        self._batch_systems = []
        self._current_system_coords = None
        self._current_system_snapshot = None
        self._snapshot_identity_seed = 0
        self._foreign_generation_active = False
        self._translation_cache = {}
        self._translation_cache_hits = 0
        self._translation_cache_misses = 0
        self._adjective_file_cache = {}
        self._capture_enabled = False
        self._batch_mode_enabled = True     # v1 flag, always on in 2.0
        self._system_saved_to_batch = False
        self._pending_extraction = False
        self._cached_solar_system = None
        self._cached_sys_data_addr = None
        self._status_display = "Loading"
        self._game_mode = "Normal"
        self._output_dir = Path.home() / "Documents" / "Haven-Extractor"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # ---- 2.0 systems ---------------------------------------------------
        self.state = ExtractorState()
        self.state.version = __version__
        self.events = EventBus()
        self._install_root = Path(__file__).resolve().parent.parent
        self._env_path = self._install_root / ENV_FILENAME
        self.env = load_env(str(self._env_path))
        self._migrate_1x_key()
        self.sync = HavenSyncClient(self.env.get("HAVEN_API_URL"),
                                    self.env.get("HAVEN_API_KEY", ""))
        self._hook_last_fire = {}
        self._enqueued_glyphs = set()
        self._refresh_requested = False
        self._upload_q = queue.Queue()
        self._journal_path = self._output_dir / "staged_queue_v2.jsonl"
        self._journal_lock = threading.Lock()
        self._stop = threading.Event()
        self._readiness_lock = threading.Lock()
        self._readiness_reported = False
        self._refused_glyphs = set()

        # deps health (no runtime pip installs in 2.0 — this surfaces instead)
        # 2.1.0: the framework pin is part of deps. Every struct read goes through
        # nmspy's generated classes, so "installed nmspy == the build this mod was
        # verified against" IS correctness. A mismatch holds all uploads.
        self._framework_ok, self._framework_detail = framework_status()
        deps_detail = {"nms_namegen": NMS_NAMEGEN_AVAILABLE}
        deps_detail.update(self._framework_detail)
        self.state.update(deps_ok=(NMS_NAMEGEN_AVAILABLE and self._framework_ok),
                          deps_detail=deps_detail)
        if not NMS_NAMEGEN_AVAILABLE:
            self.events.emit("HEALTH", "numpy/nms_namegen unavailable — procedural "
                                       "names degrade to System_<glyph>", level="error")
        if not self._framework_ok:
            self.events.emit(
                "HEALTH",
                f"FRAMEWORK MISMATCH — nmspy {self._framework_detail.get('nmspy')} installed, "
                f"this extractor needs {self._framework_detail.get('nmspy_pin')} "
                f"(pymhf {self._framework_detail.get('pymhf')} / needs "
                f"{self._framework_detail.get('pymhf_pin')}). Captures are HELD, not "
                f"uploaded. Close the game and start it with RUN_HAVEN_EXTRACTOR.bat — "
                f"the launcher installs the right version.",
                level="error", coalesce_key="framework-mismatch")

        self.events.emit("SESSION", f"Haven Extractor v{__version__} loading")
        self._load_adjective_cache()

        # local API (defensive bind — never let a failure escape into pyMHF)
        try:
            self.local_api = LocalApi(self.state, self.events, on_pair=self._on_pair)
            port = self.local_api.start(int(self.env.get("HAVEN_LOCAL_PORT", "8770")))
            if port:
                self.events.emit("SESSION", f"Local API on 127.0.0.1:{port} — "
                                            f"control at havenmap.online/extractor")
        except Exception as e:
            logger.error(f"local api failed to start: {e}")

        if self.sync.api_key:
            self.state.update(linked=True)
            self.events.emit("ACCOUNT", f"Using saved key from {ENV_FILENAME} "
                                        f"({self.sync.api_key[:16]}…)")
        else:
            self.events.emit("ACCOUNT", "Not linked — open havenmap.online/extractor "
                                        "and hit 'Link this PC'", level="warning")

        self._reload_journal()
        threading.Thread(target=self._uploader_loop, name="HavenUploader", daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, name="HavenHeartbeat", daemon=True).start()
        threading.Thread(target=self._command_loop, name="HavenCommands", daemon=True).start()
        threading.Thread(target=self._readiness_check, name="HavenReadiness", daemon=True).start()
        threading.Thread(target=self._settle_loop, name="HavenSettle", daemon=True).start()

        self._status_display = "Ready (pending hook check)"
        logger.info(f"Haven Extractor v{__version__} loaded — readiness check pending")

    def _migrate_1x_key(self):
        """First-run inheritance from a 1.x install (EXTRACTOR_2_0.md phase 4).

        The old Documents\\Haven-Extractor\\config.json holds the user's
        vh_live_ key. If haven.env has no key yet, adopt the old one — the
        server-side key->profile binding already exists, so an upgrading
        member is linked and syncing with zero manual steps. The old file is
        left untouched (never delete user files); it simply goes inert.
        """
        if self.env.get("HAVEN_API_KEY"):
            return
        old_config = self._output_dir / "config.json"
        try:
            if not old_config.exists():
                return
            with open(old_config, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_key = (old.get("api_key") or "").strip()
            if old_key.startswith("vh_live_"):
                self.env["HAVEN_API_KEY"] = old_key
                save_env(str(self._env_path), self.env)
                self.events.emit("ACCOUNT",
                                 f"Imported your 1.x extractor key ({old_key[:16]}…) — "
                                 f"captures sync to your existing Haven identity. "
                                 f"Claim/log in at havenmap.online/extractor to manage them.")
                logger.info("Migrated 1.x api_key from Documents config.json into haven.env")
        except Exception as e:
            logger.warning(f"1.x key migration skipped: {e}")

    # ------------------------------------------------------- readiness gate

    def _run_readiness_check(self, trigger: str):
        """The gate (D16): no READY unless every required hook is in pyMHF's
        registry AND the framework matches the pin.

        2.1.0: membership in ``hook_manager.hooks`` is the truth. The 2.0.x gate
        read ``failed_hooks``, which pyMHF never fills for an unresolved pattern
        (it logs 'Unable to find offset for X' and returns), so a game update left
        the mod reporting READY with dead hooks. See readiness.py.
        """
        names = None
        try:
            from pymhf.core.hooking import hook_manager
            names = registry_names(hook_manager)
        except Exception as e:
            logger.warning(f"readiness check could not read hook registry: {e}")
        failed = unbound_required_hooks(names, REQUIRED_HOOKS)
        if failed is None:
            failed = []
            bound = len(REQUIRED_HOOKS)  # can't disprove; last-fire watchdog still covers us
            logger.warning("readiness: hook registry unreadable — assuming bound")
        else:
            bound = len(REQUIRED_HOOKS) - len(failed)
        # Count only OUR mod against expectation — pyMHF's registry includes
        # its own internal mods, which inflated the count (3/1 on first run).
        mods_loaded = 1
        try:
            from pymhf.core.mod_loader import mod_manager
            registry = getattr(mod_manager, "mods", {}) or {}
            ours = [k for k in registry if "HavenExtractor2" in str(k)]
            mods_loaded = 1 if ours or not registry else 0
        except Exception:
            pass
        self.state.update(hooks_bound=bound, hooks_expected=len(REQUIRED_HOOKS),
                          mods_loaded=mods_loaded, mods_expected=1, game_running=True)
        with self._readiness_lock:
            first_report = not self._readiness_reported
            self._readiness_reported = True
        if failed:
            self._status_display = f"BROKEN: {failed[0]} not bound"
            self.events.emit("HEALTH",
                             f"MOD BROKEN — hook(s) failed to bind: {', '.join(failed)}. "
                             f"The game build no longer matches this extractor; captures "
                             f"will NOT work until the extractor is updated "
                             f"(check: {trigger}).", level="error",
                             coalesce_key="hooks-broken")
        elif not self._framework_ok:
            self._status_display = "BROKEN: framework mismatch"
            self.events.emit("HEALTH",
                             f"NOT READY — hooks {bound}/{len(REQUIRED_HOOKS)} bound but "
                             f"nmspy {self._framework_detail.get('nmspy')} != pin "
                             f"{self._framework_detail.get('nmspy_pin')}; uploads held "
                             f"(check: {trigger}).", level="error",
                             coalesce_key="framework-mismatch")
        else:
            self._status_display = "Ready"
            if first_report or trigger == "fully_booted":
                self.events.emit("SESSION",
                                 f"READY — hooks {bound}/{len(REQUIRED_HOOKS)} bound, "
                                 f"nmspy {self._framework_detail.get('nmspy')} == pin, "
                                 f"deps {'ok' if self.state.deps_ok else 'MISSING'} "
                                 f"(check: {trigger})")

    def _readiness_check(self):
        """Fallback timer: if the game never reaches the mode selector while we
        are watching (hot reload, mod loaded mid-session), still verify ~15s after
        load. The primary trigger is @on_fully_booted below."""
        time.sleep(15)
        with self._readiness_lock:
            already = self._readiness_reported
        if not already:
            self._run_readiness_check("timer")

    # ------------------------------------------------------- decorated hooks

    @nms.cTkLanguageManagerBase.Translate.after
    def on_translate(self, this, lpacText, lpacDefaultReturnValue, _result_):
        self._hook_last_fire["translate"] = time.time()
        return self._on_translate_impl(this, lpacText, lpacDefaultReturnValue, _result_)

    @nms.cGcSolarSystem.Generate.after
    def on_system_generate(self, this, lbUseSettingsFile, lSeed):
        self._hook_last_fire["generate"] = time.time()
        result = self._on_system_generate_impl(this, lbUseSettingsFile, lSeed)
        try:
            self._drain_batch_to_sync()          # previous system froze on warp
            self._publish_current(warp=True)
        except Exception as e:
            logger.error(f"[2.0] post-warp publish failed: {e}")
        return result

    @nms.cGcPlanetGenerator.GenerateCreatureRoles.after
    def on_creature_roles_generate(self, this, lPlanetData, lUA):
        self._hook_last_fire["creature_roles"] = time.time()
        result = self._on_creature_roles_generate_impl(this, lPlanetData, lUA)
        try:
            self._publish_current()
        except Exception as e:
            logger.debug(f"[2.0] publish failed: {e}")
        return result

    @on_fully_booted
    def on_fully_booted(self):
        """pyMHF's MODESELECTOR trigger: hook registration is final by now, so
        this is the authoritative moment to verify binding (2.1.0)."""
        self._hook_last_fire["fully_booted"] = time.time()
        try:
            self._run_readiness_check("fully_booted")
        except Exception as e:
            logger.error(f"[2.1] readiness check failed: {e}")

    @on_state_change("APPVIEW")
    def on_appview(self):
        self._hook_last_fire["appview"] = time.time()
        result = self._on_appview_impl()
        try:
            self._detect_game_mode()             # REVIVED (dead code in 1.x)
            if self._refresh_requested:
                self._refresh_requested = False
                self._auto_refresh_for_export()
                self.events.emit("SESSION", "Adjectives refreshed (website command)")
            self._drain_batch_to_sync()          # appview auto-saves the system
            self._publish_current()
        except Exception as e:
            logger.error(f"[2.0] appview post-processing failed: {e}")
        return result

    def _flush_current_system(self, force=False, reason=""):
        """Freeze + stage the current system WITHOUT waiting for the next warp.

        1.x users had the Export button as the manual freeze moment; 2.0
        removed it, which left a single visited system stuck in memory until
        a second warp (Parker's report, 2026-08-08). The freeze
        (_save_current_system_to_batch) is pure — it reads only the cached
        dicts, never game memory — so it is safe from any thread.
        """
        try:
            if not self._current_system_coords or not self._captured_planets:
                return False
            self._save_current_system_to_batch(force_update=force)
            self._drain_batch_to_sync()
            # Mark saved so the settle loop doesn't re-flush every cycle
            # (2.0.1 respammed "flushed current system" every 15s).
            self._system_saved_to_batch = True
            if reason:
                logger.info(f"[2.0] flushed current system ({reason})")
            return True
        except Exception as e:
            logger.error(f"[2.0] flush failed: {e}")
            return False

    def _settle_loop(self):
        """Auto-stage the current system once captures settle (~45s quiet),
        so a single system syncs without a second warp."""
        while not self._stop.wait(15):
            try:
                if self._system_saved_to_batch or not self._captured_planets:
                    continue
                last = self.state.last_capture_at
                if not last:
                    continue
                try:
                    age = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(last)).total_seconds()
                except ValueError:
                    continue
                if age > 45:
                    if self._flush_current_system(reason="captures settled"):
                        self.events.emit("CAPTURE", "System settled — staged without "
                                                    "waiting for the next warp")
            except Exception as e:
                logger.debug(f"settle loop: {e}")

    # ----------------------------------------------- live state publication

    _last_publish = 0.0

    def _publish_current(self, warp=False):
        """Mirror capture state into the local-API snapshot (throttled — the
        creature-roles hook fires ~60/sec; the panel needs ~2 Hz)."""
        now = time.time()
        if not warp and now - self._last_publish < 0.5:
            return
        self._last_publish = now
        coords = self._current_system_coords or {}
        snap = self._current_system_snapshot or {}
        game_mode = self._game_mode
        reality = "Permadeath" if game_mode == "Permadeath" else "Normal"
        system = {**{k: v for k, v in snap.items() if not k.startswith('_')},
                  **coords, "game_mode": game_mode, "reality": reality}
        if warp:
            name = system.get("system_name") or "…"
            self.state.begin_system(system)
            self.events.emit("CAPTURE",
                             f"Warp → {name} · {system.get('glyph_code') or '…'} · "
                             f"{system.get('galaxy_name') or 'galaxy resolving…'}")
        else:
            self.state.update(current_system=system or None)
            planets = []
            for p in self._captured_planets.values():
                q = dict(p)
                q.setdefault("sentinel_level", q.get("sentinel"))
                q.setdefault("flora_level", q.get("flora"))
                q.setdefault("fauna_level", q.get("fauna"))
                planets.append(q)
            known = {x.get("planet_name") for x in self.state.current_planets}
            fresh = [q for q in planets
                     if q.get("planet_name") and q["planet_name"] not in known]
            for q in fresh:
                self.events.emit("CAPTURE",
                                 f"Captured: {q['planet_name']}"
                                 f"{' (moon)' if q.get('is_moon') else ''} — "
                                 f"{q.get('biome', '?')}")
            self.state.update(current_planets=planets, capturing=self._capture_enabled)
            if fresh:
                self.state.update(
                    last_capture_at=datetime.now(timezone.utc).isoformat())

    # --------------------------------------------------- staging sync plane

    def _drain_batch_to_sync(self):
        """Move newly frozen batch entries into the upload queue + journal.
        The freeze itself is the verbatim v1 path (_save_current_system_to_batch);
        this replaces the old Export button with auto-staging (D1/D3)."""
        game_mode = self._game_mode
        reality = "Permadeath" if game_mode == "Permadeath" else "Normal"
        from payload.extraction_core import galaxy_is_known
        for entry in list(self._batch_systems):
            glyph = entry.get("glyph_code") or ""
            if not glyph or glyph in self._enqueued_glyphs:
                continue
            if glyph == "000000000000":
                self.events.emit("HEALTH", f"Dropped capture with null glyph "
                                           f"(coordinate resolution failed)", level="warning",
                                 coalesce_key="null-glyph")
                continue
            # v1's export HOLD, preserved: a galaxy-unknown system must never
            # be staged (the server would default it to Euclid). The frozen
            # entry updates in place when the galaxy resolves on a later hook
            # fire, and this drain runs again after every warp/appview.
            if not galaxy_is_known(entry):
                self.events.emit("HEALTH",
                                 f"HELD: galaxy unresolved for "
                                 f"{entry.get('system_name') or glyph} — warp out "
                                 f"and back in to fix, it will stage automatically.",
                                 level="warning", coalesce_key=f"held-{glyph}")
                continue
            # 2.1.0 framework hold: with a mismatched nmspy every struct read is
            # suspect, so nothing leaves this machine until the launcher fixes it.
            if not self._framework_ok:
                self.events.emit("HEALTH",
                                 f"HELD: {entry.get('system_name') or glyph} not staged — "
                                 f"framework mismatch (nmspy "
                                 f"{self._framework_detail.get('nmspy')} != "
                                 f"{self._framework_detail.get('nmspy_pin')}). Restart via "
                                 f"RUN_HAVEN_EXTRACTOR.bat to update.",
                                 level="error", coalesce_key="held-framework")
                continue
            # 2.1.0 upload sanity gate: refuse implausible captures LOUDLY instead of
            # uploading plausible garbage (the whole point of dropping hand offsets).
            problems = payload_sanity_problems(entry)
            if problems:
                if glyph not in self._refused_glyphs:
                    self._refused_glyphs.add(glyph)
                    self.state.update(refused_count=len(self._refused_glyphs))
                    logger.error(f"[SANITY] REFUSED {glyph}: {problems}")
                    self.events.emit("HEALTH",
                                     f"REFUSED upload of {entry.get('system_name') or glyph}: "
                                     f"{'; '.join(problems[:4])}"
                                     f"{' …' if len(problems) > 4 else ''}. This looks like "
                                     f"a struct misread (game/framework layout changed) — "
                                     f"update the extractor via RUN_HAVEN_EXTRACTOR.bat.",
                                     level="error", coalesce_key=f"refused-{glyph}")
                continue
            soft = payload_soft_warnings(entry)
            if soft:
                self.events.emit("CAPTURE",
                                 f"{entry.get('system_name') or glyph}: "
                                 f"{len(soft)} planet field(s) read as raw enum "
                                 f"({soft[0]}{' …' if len(soft) > 1 else ''}) — staged, "
                                 f"flagged for review.",
                                 level="warning", coalesce_key=f"soft-{glyph}")
            payload = dict(entry)
            payload["game_mode"] = game_mode
            payload["reality"] = reality
            payload["extractor_version"] = __version__
            self._enqueued_glyphs.add(glyph)
            self._journal_append(payload)
            self._upload_q.put(payload)
            self.state.update(batch_count=len(self._enqueued_glyphs))
            self.state.finish_system(staged=False)  # staged flips true on sync OK

    def _uploader_loop(self):
        backoff = 5
        while not self._stop.is_set():
            try:
                payload = self._upload_q.get(timeout=2)
            except queue.Empty:
                continue
            if not self.sync.api_key:
                # Not linked yet — requeue quietly and wait.
                self._upload_q.put(payload)
                self.events.emit("SYNC", "Captures waiting — extractor not linked yet "
                                         "(Link this PC on havenmap.online/extractor)",
                                 level="warning", coalesce_key="unlinked")
                time.sleep(10)
                continue
            name = payload.get("system_name") or payload.get("glyph_code")
            try:
                result = self.sync.stage(payload)
                self._journal_remove(payload.get("glyph_code"))
                self.state.update(last_sync_at=datetime.now(timezone.utc).isoformat())
                self.state.finish_system(staged=True)
                dd = result.get("dedup_status")
                note = {"already_charted": " · already charted (submit = edit)",
                        "pending_merge": " · merges into a pending submission"}.get(dd, "")
                self.events.emit("SYNC", f"Staged to Haven: {name} "
                                         f"({result.get('planet_count', 0)}p"
                                         f"+{result.get('moon_count', 0)}m){note}")
                backoff = 5
            except SyncError as e:
                self._upload_q.put(payload)
                self.events.emit("SYNC", f"Stage failed for {name}: {e} — retrying",
                                 level="warning", coalesce_key="stage-fail")
                time.sleep(min(backoff, 120))
                backoff = min(backoff * 2, 120)

    # Crash safety: unsent captures persist to disk and reload on next launch
    # (fixes the 1.x total-loss-on-crash gap — the batch lived only in memory).

    def _journal_append(self, payload):
        with self._journal_lock:
            try:
                with open(self._journal_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")
            except OSError as e:
                logger.warning(f"journal append failed: {e}")

    def _journal_remove(self, glyph):
        with self._journal_lock:
            try:
                if not self._journal_path.exists():
                    return
                kept = []
                with open(self._journal_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            if json.loads(line).get("glyph_code") != glyph:
                                kept.append(line)
                        except json.JSONDecodeError:
                            continue
                with open(self._journal_path, "w", encoding="utf-8") as f:
                    f.writelines(kept)
            except OSError as e:
                logger.warning(f"journal rewrite failed: {e}")

    def _reload_journal(self):
        try:
            if not self._journal_path.exists():
                return
            count = 0
            with open(self._journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    glyph = payload.get("glyph_code")
                    if glyph and glyph not in self._enqueued_glyphs:
                        self._enqueued_glyphs.add(glyph)
                        self._upload_q.put(payload)
                        count += 1
            if count:
                self.state.update(batch_count=len(self._enqueued_glyphs))
                self.events.emit("SYNC", f"Recovered {count} unsent capture(s) from the "
                                         f"journal (previous session)")
        except OSError as e:
            logger.warning(f"journal reload failed: {e}")

    # ------------------------------------------------------ account plane

    def _on_pair(self, token):
        """Local API hands us a pairing token from the logged-in browser."""
        result = self.sync.handshake(token)
        profile = result.get("profile") or {}
        if result.get("key"):
            self.env["HAVEN_API_KEY"] = result["key"]
            save_env(str(self._env_path), self.env)
            self.events.emit("ACCOUNT", f"Key provisioned and saved to {ENV_FILENAME} "
                                        f"({result.get('key_prefix', '')}…)")
        self.state.update(username=profile.get("username"), linked=True)
        self.events.emit("ACCOUNT", f"Linked to Haven profile '{profile.get('username')}'")
        return {"status": "ok", "profile": profile}

    def _heartbeat_loop(self):
        while not self._stop.wait(30):
            if not self.sync.api_key:
                continue
            try:
                self.sync.heartbeat({
                    "version": __version__,
                    "hooks_bound": self.state.hooks_bound,
                    "hooks_expected": self.state.hooks_expected,
                    "deps_ok": self.state.deps_ok,
                    "last_capture_at": self.state.last_capture_at,
                })
            except SyncError as e:
                self.events.emit("HEALTH", f"Heartbeat failed: {e}", level="warning",
                                 coalesce_key="hb-fail")

    def _command_loop(self):
        while not self._stop.wait(5):
            if not self.sync.api_key:
                continue
            try:
                commands = self.sync.poll_commands()
            except SyncError:
                continue
            acked = []
            for cmd in commands:
                acked.append(cmd["id"])
                name = cmd.get("command")
                if name == "sync_now":
                    if self._flush_current_system(force=True, reason="website Sync Now"):
                        self.events.emit("SYNC", "Sync Now: current system staged")
                    else:
                        self.events.emit("SYNC", "Sync Now: nothing to stage yet "
                                                 "(no captured system in memory)",
                                         level="warning")
                elif name == "refresh_mod":
                    self._refresh_requested = True   # applied on next APPVIEW (game thread)
                    self.events.emit("SESSION", "Refresh queued — applies next time you "
                                                "enter game view")
                elif name == "clear_batch":
                    drained = 0
                    try:
                        while True:
                            self._upload_q.get_nowait()
                            drained += 1
                    except queue.Empty:
                        pass
                    self._batch_systems[:] = []
                    self._enqueued_glyphs.clear()
                    with self._journal_lock:
                        try:
                            self._journal_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    self.state.update(batch_count=0)
                    self.events.emit("SYNC", f"Batch cleared by website command "
                                             f"({drained} unsent dropped)")
                else:
                    self.events.emit("SESSION", f"Unknown command '{name}' ignored",
                                     level="warning")
            if acked:
                try:
                    self.sync.ack_commands(acked)
                except SyncError:
                    pass
