"""Extractor 2.0 simulator — fake captures through the REAL pipeline.

Runs the genuine mod2 code (state, event bus, local API server, sync client,
extraction_core payload build) outside the game, generating plausible warp
captures on a timer. This is the dev/demo harness for the website console:
pair from the Extractor hub page, watch the Terminal + Current System pages
live, review the Batch Preview, submit to pending.

Only capture/ (game-memory reads) is simulated — everything else here IS the
2.0 code that ships.

Usage:
    py mod/tests/simulate.py --api http://127.0.0.1:8005 --interval 20
"""

import argparse
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # mod/ (this file lives in mod/tests)

from state import ExtractorState                     # noqa: E402
from telemetry.events import EventBus                # noqa: E402
from config.env_file import load_env, save_env       # noqa: E402
from sync.client import HavenSyncClient, SyncError   # noqa: E402
from api_local.server import LocalApi                # noqa: E402
from payload.extraction_core import (                # noqa: E402
    build_planet_entry, build_planet_list, build_system_payload,
)

# ---------------------------------------------------------------------------
# Canned NMS-flavored generation
# ---------------------------------------------------------------------------

SYLLABLES = ['uzda', 'kaya', 'ohem', 'vekk', 'ilos', 'renn', 'tau', 'moro',
             'quel', 'ashi', 'brin', 'oct', 'harn', 'yed', 'sol', 'imn']
GALAXIES = ['Euclid', 'Euclid', 'Euclid', 'Hilbert Dimension', 'Calypso', 'Eissentam']
BIOMES = ['Lush', 'Barren', 'Frozen', 'Scorched', 'Toxic', 'Irradiated', 'Marsh', 'Volcanic', 'Exotic']
WEATHERS = {'Lush': 'Balmy', 'Barren': 'Dusty', 'Frozen': 'Icy Blizzards', 'Scorched': 'Superheated Drizzle',
            'Toxic': 'Poison Rain', 'Irradiated': 'Radioactive Dust', 'Marsh': 'Humid', 'Volcanic': 'Magma Storms',
            'Exotic': 'Glitch'}
STARS = ['Yellow', 'Red', 'Green', 'Blue']
ECON = [('Trading', 'Wealthy'), ('Mining', 'Average'), ('Technology', 'Booming'), ('Fusion', 'Struggling')]
LIFE = ['Gek', 'Korvax', "Vy'keen", 'None']
RESOURCES = ['Carbon', 'Ferrite Dust', 'Gold', 'Silver', 'Chromatic Metal', 'Magnetised Ferrite',
             'Sodium', 'Dioxite', 'Paraffinium', 'Pyrite']
SENTINELS = ['Minimal', 'Limited', 'Observant', 'Aggressive']
FLORA = ['Abundant', 'Frequent', 'Sparse', 'Absent', 'Bountiful']
FAUNA = ['Frequent', 'Copious', 'Sparse', 'Absent', 'Rich']


def make_name(rng):
    n = ''.join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 3))).capitalize()
    return n


def make_glyph(rng):
    """Plausible 12-hex portal glyph: P SSS YY ZZZ XXX."""
    return (f"{rng.randint(0, 5):01X}{rng.randint(1, 0x2FF):03X}"
            f"{rng.randint(0, 0xFF):02X}{rng.randint(0, 0xFFF):03X}{rng.randint(0, 0xFFF):03X}")


def fake_captured_planet(rng, idx, is_moon=False):
    biome = rng.choice(BIOMES)
    return {
        'planet_name': f"{make_name(rng)} {['Prime', 'Minor', 'Major', 'Tau', 'IV', 'XI'][idx % 6]}",
        'biome': biome,
        'biome_subtype': 'Standard',
        'weather': WEATHERS[biome],
        'weather_display': WEATHERS[biome],
        'sentinel': rng.choice(SENTINELS),
        'flora': rng.choice(FLORA),
        'fauna': rng.choice(FAUNA),
        'flora_raw': rng.randint(0, 3),
        'common_resource': rng.choice(RESOURCES),
        'uncommon_resource': rng.choice(RESOURCES),
        'rare_resource': rng.choice(RESOURCES),
        'is_moon': is_moon,
        'planet_size': rng.choice(['Small', 'Medium', 'Large']),
    }


def identity(x):
    return x


class Simulator:
    def __init__(self, api_url, env_path, interval):
        self.rng = random.Random()
        self.interval = interval
        self.env_path = env_path
        self.env = load_env(env_path)
        if api_url:
            self.env['HAVEN_API_URL'] = api_url
        self.state = ExtractorState()
        self.events = EventBus()
        self.client = HavenSyncClient(self.env['HAVEN_API_URL'], self.env.get('HAVEN_API_KEY', ''))
        self.batch = []
        self._stop = threading.Event()

    # -- pairing (called by the local API /session route) --------------------
    def on_pair(self, token):
        result = self.client.handshake(token)
        profile = result.get('profile') or {}
        if result.get('key'):
            self.env['HAVEN_API_KEY'] = result['key']
            save_env(self.env_path, self.env)
            self.events.emit('ACCOUNT', f"Key provisioned and saved to env file ({result.get('key_prefix', '')}…)")
        self.state.update(username=profile.get('username'), linked=True)
        self.events.emit('ACCOUNT', f"Linked to Haven profile '{profile.get('username')}'")
        return {'status': 'ok', 'profile': profile}

    # -- background loops ----------------------------------------------------
    def heartbeat_loop(self):
        while not self._stop.wait(30):
            if not self.client.api_key:
                continue
            try:
                self.client.heartbeat({
                    'version': self.state.version,
                    'hooks_bound': self.state.hooks_bound,
                    'hooks_expected': self.state.hooks_expected,
                    'deps_ok': self.state.deps_ok,
                    'last_capture_at': self.state.last_capture_at,
                })
            except SyncError as e:
                self.events.emit('HEALTH', f"Heartbeat failed: {e}", level='warning',
                                 coalesce_key='hb-fail')

    def command_loop(self):
        while not self._stop.wait(5):
            if not self.client.api_key:
                continue
            try:
                commands = self.client.poll_commands()
            except SyncError:
                continue
            acked = []
            for cmd in commands:
                acked.append(cmd['id'])
                if cmd['command'] == 'refresh_mod':
                    self.events.emit('SESSION', 'Refresh requested from the website — mod reloaded (simulated)')
                elif cmd['command'] == 'clear_batch':
                    self.batch.clear()
                    self.state.update(batch_count=0)
                    self.events.emit('SYNC', 'Batch cleared by website command')
                else:
                    self.events.emit('SESSION', f"Unknown command '{cmd['command']}' ignored", level='warning')
            if acked:
                try:
                    self.client.ack_commands(acked)
                except SyncError:
                    pass

    # -- the fake warp/capture cycle (the only simulated part) ---------------
    def capture_cycle(self):
        rng = self.rng
        name = make_name(rng)
        glyph = make_glyph(rng)
        galaxy = rng.choice(GALAXIES)
        star = rng.choice(STARS)
        econ_type, econ_strength = rng.choice(ECON)
        life = rng.choice(LIFE)
        no_trade = life == 'None'

        self.events.emit('CAPTURE', f"Warp → {name} · {glyph} · {galaxy}")
        coords = {
            'system_name': name,
            'region_name': f"{make_name(rng)} {rng.choice(['Expanse', 'Anomaly', 'Cluster', 'Boundary'])}",
            'glyph_code': glyph,
            'galaxy_name': galaxy,
            'galaxy_index': GALAXIES.index(galaxy) % 6,
            'galaxy_unknown': False,
            'voxel_x': rng.randint(-2000, 2000), 'voxel_y': rng.randint(-120, 120),
            'voxel_z': rng.randint(-2000, 2000),
            'region_x': rng.randint(0, 4095), 'region_y': rng.randint(0, 255),
            'region_z': rng.randint(0, 4095),
            'solar_system_index': rng.randint(1, 600),
        }
        snapshot = {
            'system_name': name, 'star_color': star,
            'economy_type': econ_type, 'economy_strength': econ_strength,
            'conflict_level': rng.choice(['Low', 'Medium', 'High', 'Pirate']),
            'dominant_lifeform': life, 'system_seed': rng.getrandbits(48),
            'no_trade_data': no_trade,
        }
        self.state.begin_system({**coords, **snapshot,
                                 'game_mode': 'Normal', 'reality': 'Normal'})

        captured = {}
        n_planets = rng.randint(1, 5)
        n_moons = rng.randint(0, min(2, 6 - n_planets))
        for i in range(n_planets + n_moons):
            time.sleep(min(2.0, self.interval / 10))
            planet = fake_captured_planet(rng, i, is_moon=i >= n_planets)
            captured[planet['planet_name']] = planet
            display = dict(planet)
            display['sentinel_level'] = planet['sentinel']
            display['flora_level'] = planet['flora']
            display['fauna_level'] = planet['fauna']
            self.state.add_planet(display)
            self.events.emit('CAPTURE',
                             f"Captured {i + 1}: {planet['planet_name']}"
                             f"{' (moon)' if planet['is_moon'] else ''} — {planet['biome']}")

        # ---- the REAL payload pipeline (verbatim extraction_core) ----------
        def builder(c, idx):
            return build_planet_entry(
                c, idx, translate_resource=identity,
                biome_plant_resource={}, biome_subtype_plant_override={},
                hidden_substance_names=(), hidden_substance_ids=(),
                clean_weather=identity)

        planets = build_planet_list(captured, planet_builder=builder,
                                    count_hint=n_planets + n_moons)
        payload = build_system_payload(
            snapshot=snapshot, coords=coords, planets=planets,
            extractor_version=self.state.version,
            procedural_name=name, has_captured=True,
            now_iso=datetime.now(timezone.utc).isoformat(),
            now_ts=int(time.time()), trigger='auto_stage')
        payload['reality'] = 'Normal'
        payload['game_mode'] = rng.choice(['Normal', 'Normal', 'Survival'])

        self.batch.append(payload)
        self.state.update(batch_count=len(self.batch))

        if not self.client.api_key:
            self.events.emit('SYNC', 'Captured but NOT synced — extractor not linked yet '
                                     '(open the Extractor page and hit Link this PC)',
                             level='warning', coalesce_key='unlinked')
            self.state.finish_system(staged=False)
            return

        try:
            result = self.client.stage(payload)
            self.state.update(last_sync_at=datetime.now(timezone.utc).isoformat())
            dd = result.get('dedup_status')
            suffix = {'already_charted': ' · already charted (would edit)',
                      'pending_merge': ' · merges into a pending submission'}.get(dd, '')
            self.events.emit('SYNC', f"Staged to Haven: {name} ({result.get('planet_count')}p"
                                     f"+{result.get('moon_count')}m){suffix}")
            self.state.finish_system(staged=True)
        except SyncError as e:
            self.events.emit('SYNC', f"Stage failed for {name}: {e}", level='error')
            self.state.finish_system(staged=False)

    def run(self):
        # Simulated load sequence — real capture/ wires these from pyMHF.
        self.events.emit('SESSION', f"Haven Extractor v{self.state.version} loading (simulator)")
        self.state.update(mods_loaded=1, mods_expected=1, hooks_bound=4, hooks_expected=4,
                          deps_ok=True, game_running=True,
                          deps_detail={'numpy': True, 'hgpaktool': True, 'adjective_cache': True})
        api = LocalApi(self.state, self.events, on_pair=self.on_pair)
        port = api.start(int(self.env.get('HAVEN_LOCAL_PORT', '8770')))
        if port:
            self.events.emit('SESSION', f"Local API on 127.0.0.1:{port}")
        if self.client.api_key:
            self.state.update(linked=True)
            self.events.emit('ACCOUNT', f"Using saved key from env file ({self.client.api_key[:16]}…)")
        else:
            self.events.emit('ACCOUNT', 'Not linked — open havenmap Extractor page and hit "Link this PC"',
                             level='warning')
        self.events.emit('SESSION', 'READY — all hooks bound (4/4), deps present')

        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self.command_loop, daemon=True).start()

        print(f"[sim] local API: http://127.0.0.1:{port} | haven: {self.client.api_url}")
        print("[sim] Ctrl+C to stop")
        try:
            while True:
                self.capture_cycle()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            api.stop()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--api', default='http://127.0.0.1:8005')
    ap.add_argument('--env', default=str(Path(__file__).resolve().parent / 'sim_haven.env'))
    ap.add_argument('--interval', type=float, default=20.0,
                    help='seconds between simulated warps')
    args = ap.parse_args()
    Simulator(args.api, args.env, args.interval).run()
