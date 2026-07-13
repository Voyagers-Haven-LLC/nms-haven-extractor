#!/usr/bin/env python3
"""
Offline Star-Position Offset Analyzer
=====================================

Re-mines ``star_position_data.jsonl`` (written by ``star_position_research.py``)
to find which byte offset in ``cGcSolarSystemData`` — or the raw struct dump —
holds NMS's sub-voxel star position.

Why this exists
---------------
The in-game ``Analyze Positions`` button scored offsets by ``sum(|float - voxel|)``.
That is the wrong objective:

  * it needs a *correct* voxel read, and with a degenerate read (voxel = 0,0,0)
    it degrades to "find the smallest float near zero"; and
  * with captures from a *single* voxel it cannot tell a real position field from
    any other constant — both look "close to the voxel".

This tool uses the actual signature of a sub-voxel position field, which only
appears across **multiple, different** voxels:

    value(axis) ~= 1.0 * voxel(axis) + frac          (slope ~ 1, high R^2)

    ...where ``frac`` is bounded (about one voxel unit) and DETERMINISTIC per
    system: the integer part tracks the voxel, the fractional part varies by
    system index and is identical every time the same system is re-read.

A constant field (slope 0), a wrong-scale field (slope != 1), or near-zero noise
are all rejected by the slope/R^2 test — but only if you fed it captures from
>= 2 distinct voxels. The tool says so loudly when your data can't discriminate.

It reads BOTH capture formats:
  * v1: ``{voxel, vector3f_candidates:[{offset,x,y,z}]}``  (old files)
  * v2: ``{voxel, valid, raw_b64, raw_base_offset}``       (new capture path)
For v2 it re-derives float triplets at EVERY 4-aligned offset, so it is not
limited to whatever filter ran at capture time.

Pure Python 3.8+, no numpy. Run with the extractor's embedded interpreter:
  .../HavenExtractor/python/python.exe analyze_star_positions.py [data.jsonl]

Self-test (no game, no data file needed):
  python analyze_star_positions.py --self-test
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import sys
from pathlib import Path

DEFAULT_DATA = Path.home() / "Documents" / "Haven-Extractor" / "star_position_data.jsonl"

# Slope tolerance for "tracks the voxel 1:1". A real position-in-voxel-units field
# has slope ~ 1.0; we score with a gaussian so 0.85..1.15 stays strong and 0/2 die.
SLOPE_SIGMA = 0.15
# Residuals (value - voxel) above this are implausible for a one-voxel fractional part.
RES_SPAN_OK = 1.25
# Same (universe address) read twice must agree to within this to count as deterministic.
DETERMINISM_EPS = 1e-3
# An offset must appear in at least this fraction of valid captures to be scored.
PRESENCE_FRAC = 0.6


# --------------------------------------------------------------------------- #
# Loading / validation
# --------------------------------------------------------------------------- #
def load_jsonl(path):
    """Yield parsed capture dicts from a JSONL file, skipping blank/bad lines."""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                sys.stderr.write(f"  [warn] line {ln}: bad JSON, skipped\n")
    return out


def _as_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v, 0)
        except ValueError:
            return None
    return None


def is_degenerate(cap):
    """True if this capture's voxel/address read clearly failed (poisons analysis).

    Mirrors the extractor's _coords_look_valid + _decode_universe_address guards:
    all-zero universe origin, the all-FFF unread sentinel, or an explicitly
    valid=False flag from the v2 capture path.
    """
    if cap.get("valid") is False:
        return True
    ua = _as_int(cap.get("universe_addr"))
    if ua in (0, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFF):
        return True
    vox = cap.get("voxel")
    if not (isinstance(vox, (list, tuple)) and len(vox) == 3):
        return True
    vx, vy, vz = vox
    sysidx = cap.get("system_idx", 0)
    # all-FFF read decodes to (-1,-1,-1) sys 4095
    if (vx, vy, vz, sysidx) == (-1, -1, -1, 4095):
        return True
    # universe origin with no system index is the impossible-origin sentinel
    if vx == 0 and vy == 0 and vz == 0 and sysidx == 0:
        return True
    return False


# --------------------------------------------------------------------------- #
# Sample extraction: offset -> list of samples across captures
# --------------------------------------------------------------------------- #
class Sample:
    __slots__ = ("voxel", "sys", "ua", "val")

    def __init__(self, voxel, sys, ua, val):
        self.voxel = voxel  # (vx, vy, vz) ints
        self.sys = sys      # system index (int)
        self.ua = ua        # universe address (int) — identity for determinism test
        self.val = val      # (x, y, z) floats read at this offset


def _finite_triplet(t):
    return all(math.isfinite(v) and abs(v) < 1e6 for v in t)


def build_series(caps):
    """Return {offset_int: [Sample, ...]} merged across all valid captures.

    Uses raw_b64 when present (re-derives every 4-aligned float triplet);
    otherwise falls back to the pre-filtered vector3f_candidates list (v1).
    """
    series = {}
    used = 0
    for cap in caps:
        if is_degenerate(cap):
            continue
        used += 1
        voxel = tuple(cap["voxel"])
        sysidx = cap.get("system_idx", 0)
        ua = _as_int(cap.get("universe_addr")) or 0

        raw_b64 = cap.get("raw_b64")
        if raw_b64:
            base = cap.get("raw_base_offset", 0)
            blob = base64.b64decode(raw_b64)
            for off in range(0, len(blob) - 12, 4):
                t = struct.unpack_from("<fff", blob, off)
                if not _finite_triplet(t):
                    continue
                series.setdefault(base + off, []).append(Sample(voxel, sysidx, ua, t))
        else:
            for c in cap.get("vector3f_candidates", []):
                off = _as_int(c.get("offset"))
                if off is None:
                    continue
                t = (c.get("x", 0.0), c.get("y", 0.0), c.get("z", 0.0))
                if not _finite_triplet(t):
                    continue
                series.setdefault(off, []).append(Sample(voxel, sysidx, ua, t))
    return series, used


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def _linfit(xs, ys):
    """Ordinary least squares y = slope*x + intercept. Returns (slope, intercept, r2)
    or None if x has no variance (can't fit a slope from a single x value)."""
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    mean_y = sy / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    if ss_tot < 1e-12:
        r2 = 0.0  # y is constant => does not track voxel => useless as position
    else:
        r2 = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r2


def _slope_gauss(slope):
    return math.exp(-((slope - 1.0) ** 2) / (2 * SLOPE_SIGMA * SLOPE_SIGMA))


def analyze_offset(samples):
    """Score one offset's samples. Returns a metrics dict (higher 'score' = better)."""
    n = len(samples)
    distinct_voxels = len({s.voxel for s in samples})
    distinct_systems = len({(s.voxel, s.sys) for s in samples})

    axes = []
    for a in range(3):
        xs = [s.voxel[a] for s in samples]
        ys = [s.val[a] for s in samples]
        fit = _linfit(xs, ys)
        residuals = [y - x for x, y in zip(xs, ys)]
        res_span = max(residuals) - min(residuals)

        # determinism: same universe address must yield the same value
        groups = {}
        for s in samples:
            groups.setdefault(s.ua, []).append(s.val[a])
        intra = max((max(v) - min(v) for v in groups.values()), default=0.0)

        axis = {
            "has_voxel_var": fit is not None,
            "slope": fit[0] if fit else None,
            "intercept": fit[1] if fit else None,
            "r2": fit[2] if fit else None,
            "res_span": res_span,
            "intra_system_spread": intra,
        }

        if fit is not None:
            slope, _, r2 = fit
            s_axis = r2 * _slope_gauss(slope)
            if res_span > RES_SPAN_OK:
                s_axis *= max(0.0, 1.0 - (res_span - RES_SPAN_OK))
        else:
            # No voxel variation on this axis: can't confirm it tracks the voxel.
            # Give a weak placeholder so a partially-varying capture set still ranks,
            # but it can never beat an axis we actually confirmed.
            s_axis = 0.15

        if intra > DETERMINISM_EPS:
            s_axis *= 0.2  # a field that changes for the same system isn't position

        axis["axis_score"] = s_axis
        axes.append(axis)

    any_varied = any(ax["has_voxel_var"] for ax in axes)
    score = sum(ax["axis_score"] for ax in axes) / 3.0 if any_varied else 0.0

    return {
        "n": n,
        "distinct_voxels": distinct_voxels,
        "distinct_systems": distinct_systems,
        "axes": axes,
        "score": score,
    }


def rank_offsets(series, total_valid, presence_frac=PRESENCE_FRAC):
    """Score every sufficiently-present offset; return list sorted by score desc."""
    min_present = max(2, int(round(presence_frac * total_valid)))
    rows = []
    for off, samples in series.items():
        if len(samples) < min_present:
            continue
        if len({s.voxel for s in samples}) < 2:
            continue  # cannot discriminate position from constant at one voxel
        m = analyze_offset(samples)
        m["offset"] = off
        rows.append(m)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def discriminating_power(caps):
    valid = [c for c in caps if not is_degenerate(c)]
    voxels = {tuple(c["voxel"]) for c in valid}
    systems = {(tuple(c["voxel"]), c.get("system_idx")) for c in valid}
    axis_var = [len({v[a] for v in voxels}) for a in range(3)]
    return {
        "total": len(caps),
        "valid": len(valid),
        "dropped": len(caps) - len(valid),
        "distinct_voxels": len(voxels),
        "distinct_systems": len(systems),
        "axis_voxel_levels": axis_var,  # distinct voxel values seen per axis
    }


def format_report(caps, rows, dp, top=15):
    out = []
    out.append("=" * 78)
    out.append("STAR-POSITION OFFSET ANALYSIS")
    out.append("=" * 78)
    out.append(f"captures: {dp['total']}  valid: {dp['valid']}  dropped(degenerate): {dp['dropped']}")
    out.append(f"distinct voxels: {dp['distinct_voxels']}  distinct systems: {dp['distinct_systems']}")
    out.append(f"voxel levels per axis (X,Y,Z): {tuple(dp['axis_voxel_levels'])}")
    out.append("")

    if dp["valid"] < 2 or dp["distinct_voxels"] < 2:
        out.append("!! LOW DISCRIMINATING POWER -------------------------------------------")
        out.append("   All usable captures are at < 2 distinct voxels. This analysis CANNOT")
        out.append("   separate a real position field from any constant field - both look")
        out.append("   'near the voxel'. The slope/R^2 test needs the integer part to MOVE.")
        out.append("")
        out.append("   Fix: warp across several DIFFERENT voxels (different YY/ZZZ/XXX in the")
        out.append("   glyph) and capture 30-50 systems, then re-run. Also confirm the voxel")
        out.append("   read isn't failing - every dropped capture above had a bad address.")
        out.append("=" * 78)
        return "\n".join(out)

    if not rows:
        out.append("No offset was present across enough captures with >= 2 voxels.")
        out.append("=" * 78)
        return "\n".join(out)

    out.append("Top candidates (score 0..1; 1.0 = perfect slope-1 voxel tracking,")
    out.append("deterministic per system, bounded fractional offset):")
    out.append("")
    hdr = f"{'offset':>8}  {'score':>5}  {'n':>3} {'vox':>3}  " \
          f"{'slopeXYZ':>20}  {'R2 XYZ':>17}  {'detXYZ':>14}"
    out.append(hdr)
    out.append("-" * len(hdr))
    for r in rows[:top]:
        ax = r["axes"]

        def f(field, fmt):
            return "/".join(
                (fmt.format(a[field]) if a[field] is not None else "  -  ") for a in ax
            )

        slopes = f("slope", "{:.2f}")
        r2s = f("r2", "{:.2f}")
        dets = "/".join(f"{a['intra_system_spread']:.2g}" for a in ax)
        out.append(
            f"0x{r['offset']:06X}  {r['score']:.3f}  {r['n']:>3} {r['distinct_voxels']:>3}  "
            f"{slopes:>20}  {r2s:>17}  {dets:>14}"
        )
    out.append("")
    best = rows[0]
    out.append(f"BEST CANDIDATE: 0x{best['offset']:06X}  (score {best['score']:.3f})")
    if best["score"] < 0.5:
        out.append("  (weak - no offset shows clean slope-1 tracking; the position may be in")
        out.append("   a different encoding/struct. Consider the GenerateQueryInfo / voxel-root")
        out.append("   capture streams instead of scanning cGcSolarSystemData.)")
    out.append("=" * 78)
    return "\n".join(out)


def summarize_json(rows, dp, top=25):
    return {
        "discriminating_power": dp,
        "candidates": [
            {
                "offset": f"0x{r['offset']:06X}",
                "score": round(r["score"], 4),
                "n": r["n"],
                "distinct_voxels": r["distinct_voxels"],
                "axes": [
                    {
                        "slope": None if a["slope"] is None else round(a["slope"], 4),
                        "intercept": None if a["intercept"] is None else round(a["intercept"], 4),
                        "r2": None if a["r2"] is None else round(a["r2"], 4),
                        "res_span": round(a["res_span"], 4),
                        "intra_system_spread": round(a["intra_system_spread"], 6),
                    }
                    for a in r["axes"]
                ],
            }
            for r in rows[:top]
        ],
    }


# --------------------------------------------------------------------------- #
# Self-test (no game, no file)
# --------------------------------------------------------------------------- #
def _frac(sysidx):
    """Deterministic per-system fractional offset in [0,1) for each axis."""
    return (
        (sysidx * 0.137) % 1.0,
        (sysidx * 0.371) % 1.0,
        (sysidx * 0.713) % 1.0,
    )


def _make_synthetic():
    """Build captures with a KNOWN position field planted at 0x2300 plus decoys:
       0x0100 constant, 0x0200 near-zero noise, 0x0300 slope-2, 0x0400 large noise.
       Returns (captures, planted_offset)."""
    # deliberately avoid the (0,0,0) origin voxel — it's the impossible-origin sentinel
    voxels = [(4, 1, 2), (1, 0, 0), (2, 1, 0), (-3, 2, 1), (5, -2, 4), (10, 0, -7)]
    buf_len = 0x2400
    planted = 0x2300
    caps = []
    for vx, vy, vz in voxels:
        for sysidx in range(4):
            fxa, fya, fza = _frac(sysidx)
            blob = bytearray(buf_len)

            def put(off, t):
                struct.pack_into("<fff", blob, off, *t)

            # planted true position field: integer part = voxel, frac = f(sys)
            put(planted, (vx + fxa, vy + fya, vz + fza))
            # decoys
            put(0x0100, (5.0, -2.0, 13.0))                      # constant
            put(0x0200, ((sysidx * 7 % 5) * 0.13 - 0.3,         # near-zero noise
                         (vx * 3 % 7) * 0.05 - 0.1,
                         (vz * 5 % 3) * 0.07))
            put(0x0300, (2 * vx, 2 * vy, 2 * vz))               # slope 2 (wrong scale)
            put(0x0400, (vx * 137.0, vy * 90.0, vz * 211.0))    # large, slope >> 1

            # build a fake-but-valid universe address so is_degenerate() passes
            xr = vx & 0xFFF
            zr = vz & 0xFFF
            yr = vy & 0xFF
            ua = (xr) | (zr << 12) | (yr << 24) | (sysidx << 40) | (1 << 52)
            caps.append({
                "v": 2,
                "valid": True,
                "system_idx": sysidx,
                "voxel": [vx, vy, vz],
                "universe_addr": hex(ua),
                "raw_b64": base64.b64encode(bytes(blob)).decode("ascii"),
                "raw_base_offset": 0,
            })
    # one degenerate capture that must be dropped
    caps.append({"v": 2, "valid": False, "system_idx": 4095,
                 "voxel": [-1, -1, -1], "universe_addr": "0xffffffffffff",
                 "raw_b64": base64.b64encode(bytes(buf_len)).decode("ascii"),
                 "raw_base_offset": 0})
    return caps, planted


def run_self_test():
    print("Running self-test (synthetic multi-voxel data, planted offset 0x2300)...")
    caps, planted = _make_synthetic()

    dp = discriminating_power(caps)
    assert dp["dropped"] == 1, f"expected 1 degenerate drop, got {dp['dropped']}"
    assert dp["distinct_voxels"] == 6, dp

    series, used = build_series(caps)
    assert used == 24, f"expected 24 valid captures, got {used}"
    rows = rank_offsets(series, used)
    assert rows, "no candidates ranked"

    best = rows[0]["offset"]
    print(f"  best offset: 0x{best:06X}  score={rows[0]['score']:.3f}")
    assert best == planted, f"FAILED: expected 0x{planted:X}, got 0x{best:X}"

    # the planted field should score near 1.0 and decisively beat all decoys
    by_off = {r["offset"]: r["score"] for r in rows}
    assert rows[0]["score"] > 0.85, f"planted score too low: {rows[0]['score']}"
    for decoy in (0x0100, 0x0200, 0x0300, 0x0400):
        ds = by_off.get(decoy, 0.0)
        assert ds < 0.5, f"decoy 0x{decoy:X} scored too high: {ds}"
        print(f"  decoy 0x{decoy:06X}: score={ds:.3f}  (correctly rejected)")

    # v1 path: same data expressed as candidate lists (no raw_b64)
    v1 = []
    for c in caps:
        if c.get("valid") is False:
            continue
        blob = base64.b64decode(c["raw_b64"])
        cands = []
        for off in (planted, 0x0100, 0x0200, 0x0300):
            x, y, z = struct.unpack_from("<fff", blob, off)
            cands.append({"offset": hex(off), "x": x, "y": y, "z": z})
        v1.append({"system_idx": c["system_idx"], "voxel": c["voxel"],
                   "universe_addr": c["universe_addr"], "vector3f_candidates": cands})
    s2, u2 = build_series(v1)
    r2 = rank_offsets(s2, u2)
    assert r2 and r2[0]["offset"] == planted, "v1-format path failed to recover planted offset"
    print(f"  v1-format path: best 0x{r2[0]['offset']:06X}  score={r2[0]['score']:.3f}")

    # single-voxel data must report LOW discriminating power (not a false winner)
    single = [c for c in caps if c.get("valid") and tuple(c["voxel"]) == (4, 1, 2)]
    dp1 = discriminating_power(single)
    assert dp1["distinct_voxels"] == 1
    s3, u3 = build_series(single)
    r3 = rank_offsets(s3, u3)
    assert not r3, "single-voxel data should yield no confident candidates"
    print("  single-voxel data correctly yields no confident candidate")

    print("\nSELF-TEST PASSED\n")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline star-position offset analyzer")
    ap.add_argument("data", nargs="?", default=str(DEFAULT_DATA),
                    help=f"path to star_position_data.jsonl (default: {DEFAULT_DATA})")
    ap.add_argument("--self-test", action="store_true", help="run built-in self-test and exit")
    ap.add_argument("--top", type=int, default=15, help="rows to print (default 15)")
    ap.add_argument("--json", metavar="PATH", help="write full metrics JSON here")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    path = Path(args.data)
    if not path.exists():
        sys.stderr.write(f"[error] no data file at {path}\n")
        sys.stderr.write("        Run the Star Position Research tool in-game first, or pass a path.\n")
        return 2

    caps = load_jsonl(path)
    if not caps:
        sys.stderr.write("[error] no captures in file\n")
        return 2

    dp = discriminating_power(caps)
    series, used = build_series(caps)
    rows = rank_offsets(series, used)
    print(format_report(caps, rows, dp, top=args.top))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(summarize_json(rows, dp), f, indent=2)
        print(f"\n[wrote] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
