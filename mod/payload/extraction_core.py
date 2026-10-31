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

import re
from collections import Counter
from typing import Optional, List, Tuple, Dict, Any, Callable


# --- 2.3.0: system identity + name validation (pure) ---------------------------------
# Both came out of kingager's review of 2.1.2 (Oct 2026). A Budullangr capture shipped
# system_name = U+0006: the raw memory read handed back a lone control character, it
# was truthy, and it overrode the procedural name. 466 live systems carry such names.

def validated_system_name(value: Any) -> str:
    """A system name only if every character is printable; '' otherwise.

    Deliberately rejects the WHOLE value rather than stripping the bad characters:
    stripping would turn '\\x17 Everion' into 'Everion', which is just a different
    wrong name. An empty result lets the caller fall back (snapshot / procedural)."""
    if not isinstance(value, str) or not value:
        return ""
    if not all(c.isprintable() for c in value):
        return ""
    return value.strip()


def choose_system_name(coords_name: Any, snapshot_name: Any, procedural_name: Any,
                       glyph_code: str) -> str:
    """The upload's name, in the precedence the payload always used: the in-game /
    renamed name from coords, the live snapshot name, the procedural name. A
    'System_<glyph>' placeholder only wins when nothing better validates."""
    candidates = [validated_system_name(v) for v in (coords_name, snapshot_name, procedural_name)]
    for value in candidates:
        if value and not value.startswith("System_"):
            return value
    return next((v for v in candidates if v), f"System_{glyph_code or ''}")


def system_key(payload: Dict[str, Any]) -> str:
    """The identity a capture is deduplicated under: galaxy + glyph.

    The same 12-glyph address exists in every galaxy; 44 charted addresses already
    sit in more than one. Keying on the glyph alone (2.0 - 2.2) skipped the second
    galaxy's system as a duplicate. Euclid is galaxy_index 0, a valid index."""
    index = payload.get("galaxy_index")
    if payload.get("galaxy_unknown"):
        galaxy = "unknown"
    elif isinstance(index, int) and not isinstance(index, bool) and 0 <= index <= 255:
        galaxy = f"index:{index}"
    else:
        galaxy = f"name:{str(payload.get('galaxy_name') or '').strip().casefold() or '?'}"
    return f"{galaxy}|{str(payload.get('glyph_code') or '').strip().upper()}"


_UNRESOLVED_ID_RE = re.compile(r'^(?:[A-Z]+_[A-Z0-9_]+|[A-Z]+\d{1,2}|PLANETCLASS\d+)$')
_DISPLAY_FIELDS = ('flora_display', 'fauna_display', 'sentinel_display', 'weather_display',
                   'planet_description')


def capture_readiness(captured_planets: Dict[str, Any], snapshot: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Is the current system's live capture complete enough to freeze now?

    Pure. Used by the bounded late-refresh (2.3.0): PlanetInfo (the discovery-page
    words) and the plant line fill in AFTER the capture hooks, sometimes after the
    first APPVIEW. When this says no, the mod re-reads a few more times on later
    game-thread events, then freezes whatever it has - it never holds a system
    forever (Haven grades partial systems server-side).

    Complete == the body count is known, every one of those bodies is named, and
    each has its display words (not an unresolved id) and a read plant line."""
    expected = (snapshot or {}).get('_planets_count')
    if not isinstance(expected, int) or isinstance(expected, bool) or not 1 <= expected <= 6:
        return False, 'body count not read'
    named = [body for _, body in select_captures(captured_planets) if _capture_has_name(body)]
    if len(named) < expected:
        return False, f'{len(named)}/{expected} named bodies captured'
    for body in named:
        missing = [f for f in _DISPLAY_FIELDS
                   if not body.get(f) or body.get(f) == 'Unknown' or _UNRESOLVED_ID_RE.match(str(body.get(f)))]
        if body.get('plant_source') not in ('game', 'none'):
            missing.append('plant line')
        if missing:
            return False, f"{body.get('planet_name')}: missing {', '.join(missing)}"
    return True, f'{expected}/{expected} bodies with discovery details'


# Galaxy index is a 0-255 value; 0 == Euclid. UNKNOWN (None) must NEVER be silently
# turned into Euclid by callers - it means "we could not read the galaxy", which is
# a different fact from "we are in Euclid".
GALAXY_MIN = 0
GALAXY_MAX = 255


_UNRESOLVED_ID_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')


def planet_type_label(description: Optional[str], planet_class: Optional[str] = '',
                      is_moon: bool = False) -> str:
    """Compose the planet-type label the game shows from the resolved descriptor.

    The game stores descriptors as templates ("Viridescent %PLANETCLASS%") and
    fills the placeholder from PLANETCLASS1..3 ("Planet"/"Moon"/"Planetoid");
    special pools ("Infested Paradise", "Planetary Anomaly", "Gas Giant") have no
    placeholder and are shown as-is. Returns '' when the descriptor is empty or
    still an unresolved id (all caps) — never ships an id as a label.
    """
    desc = (description or '').strip()
    if not desc or desc == 'None' or _UNRESOLVED_ID_RE.match(desc):
        return ''
    cls = (planet_class or '').strip()
    if not cls or cls == 'None' or _UNRESOLVED_ID_RE.match(cls):
        cls = 'Moon' if is_moon else 'Planet'
    label = desc.replace('%PLANETCLASS%', cls)
    label = re.sub(r'<[^>]*>', '', label)
    return re.sub(r'\s+', ' ', label).strip()


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
                 "gravitino_balls", "vile_brood", "infested",
                 "high_sentinel_activity", "aggressive_sentinel_activity")
_RESOURCE_KEYS = ("common_resource", "uncommon_resource", "rare_resource")


def build_planet_entry(
    captured: Dict[str, Any],
    index: int,
    *,
    translate_resource: Callable[[str], str],
    hidden_substance_names: Any,
    hidden_substance_ids: Any,
    clean_weather: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Build one planet payload entry from a captured-planet dict.

    Resource translation and the hidden-substance fix are carried over verbatim
    from the prior ``_planet_from_captured``. This is a pure function (game-memory
    reads are done by the caller), so the same builder serves the live system AND
    every already-frozen batched system.

    ``plant_resource`` is the plant the game itself lists for the planet (the
    PLANT_* ExtraResourceHint the export refresh reads), shipped with
    ``plant_source: "game"``. A planet the game lists no plant for gets none
    (``plant_source: "none"`` once that list has been read): nothing is guessed
    from the biome. There is no ``resources`` list: the game
    field once shipped as one is the abundance word, not the resources.
    ``salvageable_scrap`` also comes from the typed cGcPlanetData.HasScrap flag.

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
    # 2.2.0 (Haven 2.16.0, one basis): `weather` is the parent = the game's weather CLASS
    # (Dust, Humid... Haven maps it to its own wording), `weather_text` the exact text the game
    # shows, never rewritten. Without a class read, the display string falls back into
    # `weather` through clean_weather exactly as before.
    weather_text = captured.get('weather_display') or ''
    weather_val = captured.get('weather') or weather_text or 'Unknown'
    if not captured.get('weather') and clean_weather is not None and weather_val and weather_val != 'Unknown':
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

    # 2.0.3: ship the captured-then-dropped fields the backend already parses
    # (approvals.py extraction ingest). Only send what was actually captured —
    # never fabricate a value for an unobserved field.
    if captured.get('is_weather_extreme'):
        result["extreme_weather"] = True
    if captured.get('storm_frequency'):
        result["storm_frequency"] = captured['storm_frequency']
    if captured.get('planet_description'):
        result["description"] = captured['planet_description']
    if weather_text:
        result["weather_text"] = weather_text
    # 2.2.1: the game's text ids behind the words (flora/fauna/sentinel/weather/description/
    # planet_class) — the same in every language; the words above are the player's language.
    text_ids = captured.get('text_ids')
    if isinstance(text_ids, dict) and text_ids:
        result["text_ids"] = {k: v for k, v in text_ids.items() if isinstance(v, str) and v}
    by_mode = captured.get('sentinels_by_mode')
    if isinstance(by_mode, dict) and by_mode:
        result["sentinels_by_mode"] = {k: v for k, v in by_mode.items() if k in ("Off", "Slow", "Normal", "Fast") and v}
    if captured.get('has_rings') is not None:
        result["has_rings"] = bool(captured['has_rings'])
    # 2.1.1: typed reads the backend columns were waiting for. None == not read
    # (key stays absent); False is a real observation and IS shipped.
    for flag in ("water_world", "is_dissonant"):
        if captured.get(flag) is not None:
            result[flag] = bool(captured[flag])
    if captured.get('exotic_trophy'):
        result["exotic_trophy"] = captured['exotic_trophy']

    for flag in _PLANET_FLAGS:
        if captured.get(flag):
            result[flag] = captured[flag]
    if captured.get('has_scrap'):
        result["salvageable_scrap"] = 1        # typed cGcPlanetData.HasScrap (2.1.1)

    # Translate resource IDs to display names.
    for res_key in _RESOURCE_KEYS:
        val = result.get(res_key)
        if val and val != "Unknown":
            result[res_key] = translate_resource(val)

    # Hidden-substance fix (matches the prior behaviour).
    for res_key in _RESOURCE_KEYS:
        if result[res_key] in hidden_substance_names or result[res_key] in hidden_substance_ids:
            result[res_key] = "Rusted Metal"

    # The plant, only as the game lists it. No hint -> no plant_resource key;
    # plant_source "none" says the list WAS read and the game lists no plant.
    if captured.get('plant_resource'):
        result["plant_resource"] = captured['plant_resource']
        result["plant_source"] = "game"
    elif captured.get('plant_source') == 'none':
        result["plant_source"] = "none"

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
    now_iso: str,
    now_ts: int,
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

    2.1.1: ``trigger`` / ``source`` / ``data_source`` / ``discoverer_name`` are gone.
    Every payload carried the same four constants and no backend path reads them
    (``source`` comes from the API key, ``discovered_by`` defaults to the same
    string). Constant fields are noise, not data.
    """
    sys_props = {k: v for k, v in (snapshot or {}).items() if not k.startswith('_')}
    coords = dict(coords or {})

    data: Dict[str, Any] = {
        "extraction_time": now_iso,
        "extractor_version": extractor_version,
        "captured_planet_count": len(planets),
        "discovery_timestamp": now_ts,
        **sys_props,   # system-level props from the live snapshot
        **coords,      # coords AFTER so a manual/actual system name overrides
        "planets": list(planets),
    }
    data["planet_count"] = len(planets)

    # --- System name resolution (mirrors the prior cache-fed precedence) ----------
    # Precedence: manual/actual name (from coords) -> live game-state name (from the
    # snapshot) -> procedural name. The coords name already folds in any in-game/actual
    # name; the snapshot name is the game's own Name field captured live.
    # 2.3.0: every candidate is validated first - a control character is not a name.
    manual_name = validated_system_name(coords.get('system_name'))
    has_manual_name = bool(manual_name) and not manual_name.startswith('System_')
    procedural_name = validated_system_name(procedural_name) or f"System_{coords.get('glyph_code', '')}"
    data['procedural_name'] = procedural_name
    data['system_name'] = choose_system_name(manual_name, sys_props.get('system_name'),
                                             procedural_name, coords.get('glyph_code', ''))

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
