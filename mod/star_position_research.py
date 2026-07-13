# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = false
# ///
"""
Star Position Research Tool  (v0.2.0)

Collects ground-truth data for reverse-engineering where NMS places each star
within its region voxel. Three independent capture streams, written to
~/Documents/Haven-Extractor/:

  1. star_position_data.jsonl   <- cGcSolarSystem.Generate (the original stream,
        now with a VALIDATED voxel and a full raw struct dump). Re-mine offline
        with utility_scripts/analyze_star_positions.py.

  2. star_voxel_root.jsonl      <- cGcGalaxyVoxelGenerator.Populate. This hands us
        a Vector3f root offset keyed by universe address directly — the actual
        galaxy-map placement input, no struct scanning required.

  3. star_generation_data.jsonl <- cGcSolarSystemGenerator.GenerateQueryInfo. Dumps
        the GenerationData struct (the system's generation query output) for offline
        mining, joined to the other streams by seed.

What changed vs v0.1.0 (and why)
  * The old `Analyze Positions` button scored offsets by distance from the voxel
    coords. When the voxel read failed (it decoded to [0,0,0] / [-1,-1,-1] all of
    last session) that collapsed to "find the float nearest zero" — which is how
    0x2300 won. Captures are now SKIPPED when the address is degenerate instead of
    being written with a zero voxel, and the heavy analysis moved offline to a tool
    that actually requires multi-voxel slope-1 tracking.
  * Streams 2 & 3 read the functions that actually position stars, instead of
    scanning cGcSolarSystemData and hoping a Vector3f falls out.

SAFETY: streams 2 & 3 hook functions whose byte-patterns are version-specific. Every
new hook is wrapped so a missing/renamed symbol DISABLES that one stream and logs a
warning, rather than aborting the whole mod load (the 1.10.4 failure mode). If the
mod still won't load in-game, set ENABLE_GENERATION_HOOKS = False below.
"""

import base64
import json
import logging
import struct
import ctypes
from datetime import datetime
from pathlib import Path
from ctypes import c_float

from pymhf import Mod
from pymhf.core.memutils import map_struct, get_addressof
from pymhf.gui.decorators import gui_button
import nmspy.data.types as nms

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path.home() / "Documents" / "Haven-Extractor"
OUTPUT_FILE = OUTPUT_DIR / "star_position_data.jsonl"       # stream 1
ROOT_FILE = OUTPUT_DIR / "star_voxel_root.jsonl"            # stream 2
GEN_FILE = OUTPUT_DIR / "star_generation_data.jsonl"        # stream 3

# Master switch for the two new (pattern-version-sensitive) hooks. If the mod fails
# to load after an NMS update, flip this to False to fall back to stream 1 only.
ENABLE_GENERATION_HOOKS = True

# Struct offsets (nmspy-confirmed, same as the main extractor uses)
SEED_OFFSET = 0x21A0   # GcSeed
NAME_OFFSET = 0x2274
STRUCT_DUMP_SIZE = 0x2400


# --------------------------------------------------------------------------- #
# Defensive hook resolution — a missing symbol disables ONE stream, never the mod
# --------------------------------------------------------------------------- #
def _safe_hook(label, getter):
    """Return the hook decorator from getter(), or an identity decorator (leaving the
    method un-hooked) if resolving it raises. Prevents a renamed/absent nmspy symbol
    from aborting the entire mod load at import time."""
    try:
        dec = getter()
        if dec is None:
            raise ValueError("resolved to None")
        return dec
    except Exception as e:  # noqa: BLE001 - intentionally broad; this is the safety net
        logger.warning(f"[STAR-POS] hook '{label}' unavailable ({e}); that capture stream is disabled")
        return lambda f: f


_VOXEL_ROOT_HOOK = (lambda f: f)
_GEN_QUERY_HOOK = (lambda f: f)
if ENABLE_GENERATION_HOOKS:
    _VOXEL_ROOT_HOOK = _safe_hook(
        "cGcGalaxyVoxelGenerator.Populate",
        lambda: nms.cGcGalaxyVoxelGenerator.Populate.after,
    )
    _GEN_QUERY_HOOK = _safe_hook(
        "cGcSolarSystemGenerator.GenerateQueryInfo",
        lambda: nms.cGcSolarSystemGenerator.GenerateQueryInfo.after,
    )


# --------------------------------------------------------------------------- #
# Pure helpers (no game state) — decode + memory reads
# --------------------------------------------------------------------------- #
def decode_universe_address(universe_addr):
    """Decode packed uint64 mUniverseAddress -> coord dict, or None if degenerate.

    Bit layout (cGcDiscoveryData.mUniverseAddress — packed GalacticAddress only):
      0-11: X region   12-23: Z region   24-31: Y region   40-51: SolarSystemIndex
      52-55: PlanetIndex+1
    Identical to haven_extractor._decode_universe_address so coords agree across tools.
    """
    if universe_addr in (0, 0xFFFFFFFFFFFFFFFF):
        return None
    x_region = universe_addr & 0xFFF
    z_region = (universe_addr >> 12) & 0xFFF
    y_region = (universe_addr >> 24) & 0xFF
    system_idx = (universe_addr >> 40) & 0xFFF
    planet_idx = max(0, ((universe_addr >> 52) & 0xF) - 1)

    voxel_x = x_region if x_region <= 0x7FF else x_region - 0x1000
    voxel_y = y_region if y_region <= 0x7F else y_region - 0x100
    voxel_z = z_region if z_region <= 0x7FF else z_region - 0x1000

    # impossible universe origin == failed read
    if voxel_x == 0 and voxel_y == 0 and voxel_z == 0 and system_idx == 0:
        return None

    glyph_code = f"{planet_idx:01X}{system_idx:03X}{y_region:02X}{z_region:03X}{x_region:03X}".upper()
    return {
        "voxel": [voxel_x, voxel_y, voxel_z],
        "region": [x_region, y_region, z_region],
        "system_idx": system_idx,
        "planet_idx": planet_idx,
        "glyph_code": glyph_code,
    }


def _read_bytes(addr, size):
    return bytes((ctypes.c_ubyte * size).from_address(addr))


def _read_vec3(addr):
    f = (c_float * 3).from_address(addr)
    return [round(f[0], 6), round(f[1], 6), round(f[2], 6)]


def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, default=str) + "\n")


class StarPositionResearch(Mod):
    """Research mod to capture star placement ground-truth from NMS."""

    __version__ = "0.2.0"

    def __init__(self):
        super().__init__()
        self._captures = []          # stream 1 (solar system data)
        self._seen_roots = set()     # dedupe stream 2 by universe address
        self._root_count = 0
        self._gen_count = 0
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Star Position Research Tool v%s loaded", self.__version__)
        logger.info("  stream 1 (sys data):   %s", OUTPUT_FILE)
        logger.info("  stream 2 (voxel root): %s", ROOT_FILE)
        logger.info("  stream 3 (gen data):   %s", GEN_FILE)
        if not ENABLE_GENERATION_HOOKS:
            logger.info("  generation hooks DISABLED (ENABLE_GENERATION_HOOKS=False)")

    # ----------------------------------------------------------------- #
    # Stream 1: cGcSolarSystem.Generate -> validated capture + raw dump
    # ----------------------------------------------------------------- #
    @nms.cGcSolarSystem.Generate.after
    def on_system_generate(self, this, lbUseSettingsFile, lSeed):
        try:
            addr = get_addressof(this)
            if not addr:
                return
            solar_system = map_struct(addr, nms.cGcSolarSystem)
            sys_data = solar_system.mSolarSystemData
            sys_data_addr = get_addressof(sys_data)
            if not sys_data_addr or sys_data_addr < 0x10000:
                return

            # universe address from first planet's discovery data
            planets = solar_system.maPlanets
            discovery_data = planets[0].mPlanetDiscoveryData
            universe_addr = int(discovery_data.mUniverseAddress)

            decoded = decode_universe_address(universe_addr)
            if decoded is None:
                # The address isn't readable yet (the known mUniverseAddress-reads-zero
                # timing on Generate). DROP the capture rather than writing a zero voxel
                # that poisons the offline analysis.
                logger.info("[STAR-POS] skip: universe address not yet valid (0x%016X)", universe_addr)
                return

            seed_bytes = _read_bytes(sys_data_addr + SEED_OFFSET, 8)
            system_seed = int.from_bytes(seed_bytes, "little")

            raw = _read_bytes(sys_data_addr, STRUCT_DUMP_SIZE)

            # small near-voxel candidate list purely for an at-a-glance log; the real
            # analysis re-derives every offset from raw_b64 offline.
            vx, vy, vz = decoded["voxel"]
            candidates = []
            for off in range(0, STRUCT_DUMP_SIZE - 12, 4):
                fx, fy, fz = struct.unpack_from("<fff", raw, off)
                if (abs(fx - vx) < 2.0 and abs(fy - vy) < 2.0 and abs(fz - vz) < 2.0
                        and not (fx == 0.0 and fy == 0.0 and fz == 0.0)):
                    candidates.append({"offset": hex(off),
                                       "x": round(fx, 6), "y": round(fy, 6), "z": round(fz, 6)})

            system_name = ""
            try:
                nm = _read_bytes(sys_data_addr + NAME_OFFSET, 128)
                nul = nm.find(0)
                system_name = nm[:nul if nul >= 0 else None].decode("utf-8", errors="ignore").strip()
            except Exception:
                pass

            capture = {
                "v": 2,
                "source": "solar_system_data",
                "valid": True,
                "timestamp": datetime.now().isoformat(),
                "system_name": system_name,
                "universe_addr": hex(universe_addr),
                "system_seed": hex(system_seed),
                "system_idx": decoded["system_idx"],
                "glyph_code": decoded["glyph_code"],
                "region": decoded["region"],
                "voxel": decoded["voxel"],
                "raw_base_offset": 0,
                "raw_b64": base64.b64encode(raw).decode("ascii"),
                "vector3f_candidates": candidates,
            }
            self._captures.append(capture)
            _append_jsonl(OUTPUT_FILE, capture)

            logger.info("[STAR-POS] captured '%s' glyph=%s voxel=%s sys=%d near-voxel_floats=%d",
                        system_name or "?", decoded["glyph_code"], decoded["voxel"],
                        decoded["system_idx"], len(candidates))
        except Exception as e:  # noqa: BLE001
            logger.error("[STAR-POS] stream1 capture failed: %s", e)

    # ----------------------------------------------------------------- #
    # Stream 2: cGcGalaxyVoxelGenerator.Populate -> Vector3f root offset by UA
    # ----------------------------------------------------------------- #
    @_VOXEL_ROOT_HOOK
    def on_voxel_populate(self, lu64UniverseAddress, lVoxelData, lRootOffset):
        try:
            ua = int(lu64UniverseAddress)
            if ua in self._seen_roots:
                return  # this fires per-voxel during galaxy map gen; dedupe by address
            self._seen_roots.add(ua)

            root_addr = get_addressof(lRootOffset)
            if not root_addr:
                return
            root = _read_vec3(root_addr)
            decoded = decode_universe_address(ua) or {}

            rec = {
                "v": 2,
                "source": "voxel_root",
                "timestamp": datetime.now().isoformat(),
                "universe_addr": hex(ua),
                "voxel": decoded.get("voxel"),
                "region": decoded.get("region"),
                "system_idx": decoded.get("system_idx"),
                "glyph_code": decoded.get("glyph_code"),
                "root_offset": root,
            }
            _append_jsonl(ROOT_FILE, rec)
            self._root_count += 1
            if self._root_count <= 50 or self._root_count % 100 == 0:
                logger.info("[STAR-POS] voxel root #%d ua=0x%016X voxel=%s root=%s",
                            self._root_count, ua, decoded.get("voxel"), root)
        except Exception as e:  # noqa: BLE001
            logger.error("[STAR-POS] stream2 (voxel root) failed: %s", e)

    # ----------------------------------------------------------------- #
    # Stream 3: cGcSolarSystemGenerator.GenerateQueryInfo -> GenerationData dump
    # ----------------------------------------------------------------- #
    @_GEN_QUERY_HOOK
    def on_ssg_query_info(self, this, lSeed, lAttributes, lData):
        try:
            seed = None
            try:
                seed_addr = get_addressof(lSeed)
                if seed_addr:
                    seed = hex(int.from_bytes(_read_bytes(seed_addr, 8), "little"))
            except Exception:
                pass

            rec = {
                "v": 2,
                "source": "generation_data",
                "timestamp": datetime.now().isoformat(),
                "seed": seed,
            }
            # GenerationData is opaque in nmspy (no field layout); dump raw bytes for
            # offline mining. cGcGalaxyAttributesAtAddress likewise — dump a small window.
            try:
                data_addr = get_addressof(lData)
                if data_addr:
                    rec["gen_data_b64"] = base64.b64encode(_read_bytes(data_addr, 0x400)).decode("ascii")
            except Exception:
                pass
            try:
                attr_addr = get_addressof(lAttributes)
                if attr_addr:
                    rec["attributes_b64"] = base64.b64encode(_read_bytes(attr_addr, 0x200)).decode("ascii")
            except Exception:
                pass

            _append_jsonl(GEN_FILE, rec)
            self._gen_count += 1
            if self._gen_count <= 25 or self._gen_count % 100 == 0:
                logger.info("[STAR-POS] generation data #%d seed=%s", self._gen_count, seed)
        except Exception as e:  # noqa: BLE001
            logger.error("[STAR-POS] stream3 (generation data) failed: %s", e)

    # ----------------------------------------------------------------- #
    # GUI
    # ----------------------------------------------------------------- #
    @gui_button("Star Position Status")
    def show_status(self):
        valid_voxels = {tuple(c["voxel"]) for c in self._captures}
        logger.info("[STAR-POS] stream1 captures: %d  (distinct voxels: %d)",
                    len(self._captures), len(valid_voxels))
        logger.info("[STAR-POS] stream2 voxel roots: %d", self._root_count)
        logger.info("[STAR-POS] stream3 generation dumps: %d", self._gen_count)
        if len(valid_voxels) < 2:
            logger.info("[STAR-POS] WARNING: < 2 distinct voxels captured this session. Warp")
            logger.info("           across DIFFERENT regions before analysing — one voxel cannot")
            logger.info("           distinguish a position field from any constant.")

    @gui_button("Analyze (offline pointer)")
    def analyze(self):
        """The real analysis runs OFFLINE — the in-game distance-to-voxel scoring was
        misleading (it rewarded near-zero floats when the voxel read failed). This button
        just reports state and how to run the proper analyzer."""
        valid_voxels = {tuple(c["voxel"]) for c in self._captures}
        logger.info("[STAR-POS] %d stream1 captures across %d distinct voxels.",
                    len(self._captures), len(valid_voxels))
        logger.info("[STAR-POS] Run the offline analyzer for real scoring:")
        logger.info("           python utility_scripts/analyze_star_positions.py "
                    "\"%s\"", OUTPUT_FILE)
        logger.info("[STAR-POS] Or just read %s — its root_offset Vector3f is the", ROOT_FILE)
        logger.info("           galaxy-map placement directly (no offset hunting needed).")
        if len(valid_voxels) < 2:
            logger.info("[STAR-POS] Collect more voxels first: warp to several different regions.")
