"""AUTO-TRANSPLANTED from mod/haven_extractor.py v1.10.6 per the verified
live-code manifest (haven-ui/docs/EXTRACTOR_2_0.md §7). Source line ranges
noted per block. Bodies are verbatim; [2.0] marks the few deliberate edits.
"""

import logging

logger = logging.getLogger("haven_extractor2")

nms_system_name = nms_region_name = nms_planet_name = None
NMS_NAMEGEN_AVAILABLE = False
try:
    from nms_namegen.system import systemName as nms_system_name
    from nms_namegen.region import regionName as nms_region_name
    from nms_namegen.planet import planetName as nms_planet_name
    NMS_NAMEGEN_AVAILABLE = True
except ImportError as e:
    # [2.0] no runtime pip install — surfaced as a HEALTH event / deps flag
    logger.error(f"[NAMEGEN] unavailable ({e}) — procedural names degrade to System_<glyph>")
