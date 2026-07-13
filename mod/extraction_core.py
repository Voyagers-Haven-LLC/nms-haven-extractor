"""
extraction_core.py - Pure data-transform logic for the Haven Extractor.

This module deliberately imports NOTHING from pymhf / nmspy / the game. Everything
here is a pure function of its inputs, so it can be unit/smoke-tested outside the
game (where pymhf is unavailable). haven_extractor.py reads game memory and feeds
the resulting plain dicts into these functions to build the upload payload.

Why this module exists
----------------------
The batch data-loss bug came from the mod *re-reading live game memory* when it
finalized a system it had already left. No Man's Sky reuses a single solar-system
object, so once you warp away the old memory holds the *next* system. The fix is to
capture each system's data while it is live, stash it in plain dicts, and then build
the payload from those dicts via the PURE functions below - never touching live
memory at finalize time. Because these functions are pure, a frozen dict cannot be
mutated by a later warp, which is exactly the property the smoke test verifies.
"""

from collections import Counter
from typing import Optional, List, Tuple, Dict, Any, Callable

# Galaxy index is a 0-255 value; 0 == Euclid. UNKNOWN (None) must NEVER be silently
# turned into Euclid by callers - it means "we could not read the galaxy", which is
# a different fact from "we are in Euclid".
GALAXY_MIN = 0
GALAXY_MAX = 255


def decide_galaxy(candidates: List[Tuple[Optional[int], str]]) -> Tuple[Optional[int], str]:
    """Resolve the galaxy index from several independent candidate reads.

    Each candidate is ``(value, source_label)`` where ``value`` is an int read from
    memory or ``None`` if that source could not be read. ``candidates`` should be
    ordered by trust (most authoritative first).

    Rules:
      1. Drop candidates that failed to read (None) or are out of range (not 0..255).
      2. If any surviving candidate is NON-zero, trust it - a non-zero value is strong
         evidence of a real non-Euclid galaxy. Prefer the value that the most sources
         agree on; tie-break by trust order.
      3. If every surviving candidate is 0, return 0 (Euclid) - a *positively read* 0.
      4. If NO candidate survived, return (None, "unknown"). The caller must treat this
         as "galaxy unknown" and must NOT fall back to Euclid.

    This kills the historical bug where a single zeroed/unpopulated read was accepted
    as a definitive Euclid and short-circuited the more authoritative sources.
    """
    valid = [(v, s) for (v, s) in candidates
             if isinstance(v, int) and not isinstance(v, bool) and GALAXY_MIN <= v <= GALAXY_MAX]
    if not valid:
        return (None, "unknown")

    nonzero = [(v, s) for (v, s) in valid if v != 0]
    if nonzero:
        counts = Counter(v for v, _ in nonzero)
        top_val, top_count = counts.most_common(1)[0]
        if top_count > 1:
            chosen = top_val
        else:
            # No agreement - take the highest-trust (first) non-zero candidate.
            chosen = nonzero[0][0]
        source = next(s for v, s in nonzero if v == chosen)
        return (chosen, source)

    # All surviving candidates positively read 0 -> genuine Euclid.
    return (0, valid[0][1])


# Optional per-planet feature flags carried through from the capture hook.
_PLANET_FLAGS = ("ancient_bones", "salvageable_scrap", "storm_crystals",
                 "gravitino_balls", "vile_brood", "infested")
_RESOURCE_KEYS = ("common_resource", "uncommon_resource", "rare_resource")


def build_planet_entry(
    captured: Dict[str, Any],
    index: int,
    *,
    translate_resource: Callable[[str], str],
    biome_plant_resource: Dict[str, str],
    biome_subtype_plant_override: Dict[str, str],
    hidden_substance_names: Any,
    hidden_substance_ids: Any,
    clean_weather: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Build one planet payload entry from a captured-planet dict.

    Resource translation, hidden-substance fix and plant-resource derivation are carried
    over verbatim from the prior ``_planet_from_captured``. This is a pure function
    (game-memory reads are done by the caller), so the same builder serves the live
    system AND every already-frozen batched system.

    Adjective display strings (the Odusto 2026-06-17 bug): NMS exposes the EXACT
    weather/sentinel/flora/fauna adjectives ("Superheated Drizzle", "Observant",
    "Abundant"...) only in the live ``PlanetInfo`` array, which the capture path reads
    via _auto_refresh_for_export and stashes already-resolved into the ``*_display``
    keys. The plain ``flora``/``fauna``/``sentinel``/``weather`` keys hold only the
    generic enum tier ("Bountiful"/"Copious"/"Limited"...). We therefore PREFER the
    collected ``*_display`` value and fall back to the enum tier only when the display
    string wasn't captured (e.g. you left the system before NMS populated PlanetInfo).
    This is what was missing - the refresh collected the real adjectives and the builder
    was discarding them. ``clean_weather`` (passed by the caller) normalises the weather
    string exactly as the legacy ``_extract_single_planet`` did; identity if omitted.
    """
    flora = captured.get('flora_display') or captured.get('flora') or 'Unknown'
    fauna = captured.get('fauna_display') or captured.get('fauna') or 'Unknown'
    sentinel = captured.get('sentinel_display') or captured.get('sentinel') or 'Unknown'
    weather_val = captured.get('weather_display') or captured.get('weather') or 'Unknown'
    if clean_weather is not None and weather_val and weather_val != 'Unknown':
        weather_val = clean_weather(weather_val)

    result = {
        "planet_index": index,
        "planet_name": captured.get('planet_name') or f"Planet_{index + 1}",
        "biome": captured.get('biome', 'Unknown'),
        "biome_subtype": captured.get('biome_subtype', 'Unknown'),
        "weather": weather_val,
        "sentinel_level": sentinel,
        "flora_level": flora,
        "fauna_level": fauna,
        "common_resource": captured.get('common_resource', '') or 'Unknown',
        "uncommon_resource": captured.get('uncommon_resource', '') or 'Unknown',
        "rare_resource": captured.get('rare_resource', '') or 'Unknown',
        "is_moon": bool(captured.get('is_moon', False)),
        "planet_size": captured.get('planet_size', 'Unknown'),
    }

    for flag in _PLANET_FLAGS:
        if captured.get(flag):
            result[flag] = captured[flag]

    # Translate resource IDs to display names.
    for res_key in _RESOURCE_KEYS:
        val = result.get(res_key)
        if val and val != "Unknown":
            result[res_key] = translate_resource(val)

    # Hidden-substance fix (matches the prior behaviour).
    for res_key in _RESOURCE_KEYS:
        if result[res_key] in hidden_substance_names or result[res_key] in hidden_substance_ids:
            result[res_key] = "Rusted Metal"

    # Derive plant_resource from biome (only if the planet actually has flora).
    biome = result.get("biome", "Unknown")
    biome_subtype = result.get("biome_subtype", "Unknown")
    plant_resource = (biome_subtype_plant_override.get(biome_subtype, "")
                      or biome_plant_resource.get(biome, ""))
    if plant_resource and captured.get('flora_raw', -1) > 0:
        result["plant_resource"] = plant_resource

    return result


def _capture_has_name(captured: Dict[str, Any]) -> bool:
    """True when a captured-planet dict carries a real (non-empty) planet name."""
    name = captured.get('planet_name')
    return bool(name) and bool(str(name).strip())


def select_captures(
    captured_planets: Dict[str, Any],
    *,
    count_hint: Optional[int] = None,
) -> List[Tuple[str, Dict[str, Any]]]:
    """Pick the real bodies out of the captured-planets dict, dropping phantom captures.

    Why this exists (the Odusto 2026-06-17 bug):
    The GenerateCreatureRoles hook fires many times per second while a system is live.
    Most fires resolve a real planet name and dedupe by name, but a fire whose planet
    name can't be read yet is stored under a synthetic ``_unnamed_N`` key with no name
    and default (Lush/Small) data. A real NMS body ALWAYS has a name, so those nameless
    captures are phantoms that would otherwise upload as ``Planet_N`` placeholders that
    inflate the planet count (Odusto: 3 real planets + 3 phantom ``Planet_4/5/6``).

    Rules (insertion / slot order preserved):
      1. Partition into NAMED (``planet_name`` present) and UNNAMED captures.
      2. If ANY named entry exists, it is a real body - drop ALL unnamed phantoms.
      3. If NOTHING got a name (a fully degraded read), fall back to the unnamed entries
         so the system isn't uploaded planetless.
      4. ``count_hint`` (the live PLANETS_COUNT = planets+moons, 1..6) is an UPPER cap
         only: it can trim an over-long list, but it is NEVER a reason to KEEP a phantom.
         The post-Voyagers count read is unreliable, so we must not pad up to it with
         placeholders - we only ever shrink.
    """
    named = [(k, v) for k, v in captured_planets.items() if _capture_has_name(v)]
    chosen = named if named else list(captured_planets.items())
    if isinstance(count_hint, int) and not isinstance(count_hint, bool) and 0 < count_hint < len(chosen):
        chosen = chosen[:count_hint]
    return chosen


def build_planet_list(
    captured_planets: Dict[str, Any],
    *,
    planet_builder: Callable,
    count_hint: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Build the ordered planet list from the captured-planets dict.

    Insertion order == capture (slot) order, matching the prior behaviour. Phantom
    (nameless) captures are filtered out by ``select_captures`` before building, so a
    spurious hook fire can no longer become a ``Planet_N`` placeholder. Each surviving
    value is built by ``planet_builder(captured, index)``.
    """
    chosen = select_captures(captured_planets, count_hint=count_hint)
    return [planet_builder(captured, i) for i, (_name, captured) in enumerate(chosen)]


def build_system_payload(
    *,
    snapshot: Optional[Dict[str, Any]],
    coords: Optional[Dict[str, Any]],
    planets: List[Dict[str, Any]],
    extractor_version: str,
    procedural_name: str,
    has_captured: bool,
    now_iso: str,
    now_ts: int,
    trigger: str = "batch_auto_save",
    source: str = "live_extraction",
) -> Dict[str, Any]:
    """Assemble the full per-system upload dict from cached, while-live data.

    ``snapshot``  - system-level props captured while the system was live
                    (star_color/economy/conflict/lifeform/system_name/no_trade_data...).
    ``coords``    - coordinate + galaxy + region + name dict captured while live.
    ``planets``   - already-built planet entries (see build_planet_list).

    NO game memory is read here. This is the single place a system becomes an
    immutable payload, which is what makes a frozen batched system immune to the
    next warp overwriting shared memory. Per-user fields (discord_username, reality,
    game_mode, ...) are still attached by the upload step, exactly as before.

    Faithful reproduction of the prior ``_save_current_system_to_batch`` assembly
    (the ``**snapshot`` then ``**coords`` merge, name resolution, procgen stash).
    """
    sys_props = {k: v for k, v in (snapshot or {}).items() if not k.startswith('_')}
    coords = dict(coords or {})

    data: Dict[str, Any] = {
        "extraction_time": now_iso,
        "extractor_version": extractor_version,
        "trigger": trigger,
        "source": source,
        "data_source": "captured_hook" if has_captured else "memory_read",
        "captured_planet_count": len(planets),
        "discoverer_name": "HavenExtractor",
        "discovery_timestamp": now_ts,
        **sys_props,   # system-level props from the live snapshot
        **coords,      # coords AFTER so a manual/actual system name overrides
        "planets": list(planets),
    }
    data["planet_count"] = len(planets)

    # --- System name resolution (mirrors the prior cache-fed precedence) ----------
    # Precedence: manual/actual name (from coords) -> live game-state name (from the
    # snapshot) -> procedural name. The coords name already folds in any in-game/actual
    # name; the snapshot name is the "In the X system" game-state string captured live.
    manual_name = (coords.get('system_name') or '')
    has_manual_name = bool(manual_name) and not manual_name.startswith('System_')
    if has_manual_name:
        data['system_name'] = manual_name
    else:
        current_name = data.get('system_name') or ''
        if (not current_name) or current_name.startswith('System_'):
            snap_name = (sys_props.get('system_name') or '')
            if snap_name and not snap_name.startswith('System_'):
                data['system_name'] = snap_name

    data['procedural_name'] = procedural_name

    current_name = data.get('system_name') or ''
    if (not current_name) or current_name.startswith('System_'):
        data['system_name'] = procedural_name

    final_name = data.get('system_name') or ''
    if procedural_name and final_name and final_name != procedural_name:
        existing_desc = data.get('description', '') or ''
        marker = f"Procedural name: {procedural_name}"
        if marker not in existing_desc:
            data['description'] = (existing_desc + ("\n" if existing_desc else "") + marker).strip()
        if has_manual_name or coords.get('custom_name_applied'):
            data['custom_name_applied'] = True

    return data


def galaxy_is_known(system_payload: Dict[str, Any]) -> bool:
    """True only if the payload carries a positively-resolved galaxy.

    Used by the export hard-stop so a galaxy-unknown system is HELD rather than
    silently uploaded as Euclid (the backend defaults a missing galaxy to Euclid).
    """
    if system_payload.get('galaxy_unknown'):
        return False
    name = system_payload.get('galaxy_name')
    return bool(name) and isinstance(name, str)
