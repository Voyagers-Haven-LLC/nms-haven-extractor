"""
Extraction Watcher - Monitor for Haven Extractor output.

This module provides functionality to watch the Haven Extractor output
directory and process new extractions as they appear.

This can be integrated into the main NMS-Save-Watcher or run standalone.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Dict, Any
from threading import Thread, Event

logger = logging.getLogger(__name__)


class ExtractionWatcher:
    """
    Watches for new extraction files from Haven Extractor.

    Usage:
        watcher = ExtractionWatcher(
            output_dir=Path.home() / "Documents" / "Haven-Extractor",
            callback=lambda data: print(f"New system: {data['system_name']}")
        )
        watcher.start()
        # ... later ...
        watcher.stop()
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        poll_interval: float = 2.0
    ):
        """
        Initialize the extraction watcher.

        Args:
            output_dir: Directory where Haven Extractor writes JSON files.
                       Defaults to ~/Documents/Haven-Extractor
            callback: Function to call when new extraction is found.
                     Receives the extracted system data as a dict.
            poll_interval: Seconds between directory polls.
        """
        self.output_dir = output_dir or Path(os.environ.get(
            "HAVEN_EXTRACTOR_OUTPUT",
            Path.home() / "Documents" / "Haven-Extractor"
        ))
        self.callback = callback
        self.poll_interval = poll_interval

        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._last_extraction_time: float = 0
        self._processed_files: set = set()

    def start(self):
        """Start watching for new extractions."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Watcher already running")
            return

        self._stop_event.clear()
        self._thread = Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        logger.info(f"Extraction watcher started. Monitoring: {self.output_dir}")

    def stop(self):
        """Stop watching."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Extraction watcher stopped")

    def is_running(self) -> bool:
        """Check if watcher is currently running."""
        return self._thread is not None and self._thread.is_alive()

    def check_once(self) -> Optional[Dict[str, Any]]:
        """
        Check for new extractions once (non-blocking).

        Returns the latest extraction if available, or None.
        """
        return self._check_for_new_extraction()

    def _watch_loop(self):
        """Main watch loop (runs in background thread)."""
        while not self._stop_event.is_set():
            try:
                extraction = self._check_for_new_extraction()
                if extraction and self.callback:
                    try:
                        self.callback(extraction)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
            except Exception as e:
                logger.error(f"Watch loop error: {e}")

            self._stop_event.wait(self.poll_interval)

    def _check_for_new_extraction(self) -> Optional[Dict[str, Any]]:
        """
        Check for new extraction files.

        Returns the newest unprocessed extraction, or None.
        """
        if not self.output_dir.exists():
            return None

        # Look for the "latest.json" file first
        latest_file = self.output_dir / "latest.json"
        if latest_file.exists():
            mtime = latest_file.stat().st_mtime
            if mtime > self._last_extraction_time:
                self._last_extraction_time = mtime
                try:
                    with open(latest_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as e:
                    logger.error(f"Failed to read {latest_file}: {e}")

        # Also check for individual extraction files
        extraction_files = sorted(
            self.output_dir.glob("extraction_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for filepath in extraction_files:
            if str(filepath) in self._processed_files:
                continue

            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._processed_files.add(str(filepath))
                return data
            except Exception as e:
                logger.error(f"Failed to read {filepath}: {e}")
                self._processed_files.add(str(filepath))  # Skip bad files

        return None

    def get_latest_extraction(self) -> Optional[Dict[str, Any]]:
        """
        Get the latest extraction without marking it as processed.

        Useful for displaying current state.
        """
        latest_file = self.output_dir / "latest.json"
        if latest_file.exists():
            try:
                with open(latest_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return None


def convert_extraction_to_haven_payload(extraction: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Haven Extractor output to Haven Control Room API payload format.

    This maps the extraction format to match what the Control Room API expects.
    """
    payload = {
        # System identification
        "name": extraction.get("system_name", "Unknown System"),
        "glyph_code": extraction.get("glyph_code", ""),
        "galaxy": extraction.get("galaxy_name", "Euclid"),

        # System properties
        "star_type": extraction.get("star_type", "Unknown"),
        "economy": extraction.get("economy_type", "Unknown"),
        "economy_strength": extraction.get("economy_strength", "Unknown"),
        "conflict_level": extraction.get("conflict_level", "Unknown"),
        "dominant_lifeform": extraction.get("dominant_lifeform", "Unknown"),

        # Coordinates (if available)
        "voxel_x": extraction.get("voxel_x"),
        "voxel_y": extraction.get("voxel_y"),
        "voxel_z": extraction.get("voxel_z"),
        "solar_system_index": extraction.get("solar_system_index"),

        # Planets
        "planet_count": extraction.get("planet_count", 0),
        "planets": []
    }

    # Convert planet data
    for planet in extraction.get("planets", []):
        planet_payload = {
            "index": planet.get("planet_index", 0),
            "name": planet.get("planet_name", ""),
            "biome": planet.get("biome", "Unknown"),
            "weather": planet.get("weather", "Unknown"),
            "sentinel_level": planet.get("sentinel_level", "Unknown"),
            "flora_level": planet.get("flora_level", "Unknown"),
            "fauna_level": planet.get("fauna_level", "Unknown"),
            "resources": {
                "common": planet.get("common_resource", ""),
                "uncommon": planet.get("uncommon_resource", ""),
                "rare": planet.get("rare_resource", "")
            }
        }
        payload["planets"].append(planet_payload)

    return payload


# ============================================================================
# Standalone testing
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    def on_extraction(data):
        print(f"\n{'='*60}")
        print(f"New Extraction: {data.get('system_name', 'Unknown')}")
        print(f"Star Type: {data.get('star_type', 'Unknown')}")
        print(f"Planets: {data.get('planet_count', 0)}")
        for planet in data.get('planets', []):
            print(f"  - {planet.get('planet_name', 'Unknown')}: {planet.get('biome', 'Unknown')}")
        print(f"{'='*60}\n")

        # Show Haven payload format
        payload = convert_extraction_to_haven_payload(data)
        print("Haven Payload:")
        print(json.dumps(payload, indent=2))

    watcher = ExtractionWatcher(callback=on_extraction)
    watcher.start()

    print("Watching for extractions... Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        print("Stopped.")
