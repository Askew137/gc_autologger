"""
Module for parsing GPX and LOC waypoint files.
Extracts GC codes, cache titles, and geographic coordinates (lat, lon).
Supports standard GPX 1.0/1.1, c:geo, GSAK, GeoGet, Locus, and Mapy.cz exports.
"""

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Dict


@dataclass
class WaypointItem:
    gccode: str
    lat: float
    lon: float
    name: str = ""
    cache_type: str = ""
    is_modified_hint: bool = False
    source_file: str = ""

    def __repr__(self) -> str:
        return f"<Waypoint {self.gccode} [{self.lat:.5f}, {self.lon:.5f}] - {self.name}>"


def _strip_namespaces(root: ET.Element) -> None:
    """Strip XML namespaces from element tags for reliable tag matching."""
    for elem in root.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]


def is_virtual_or_earth(cache_type: str) -> bool:
    """Check if a cache type string represents a Virtual, EarthCache, or Webcam cache."""
    if not cache_type:
        return False
    low = cache_type.strip().lower()
    return any(k in low for k in ["virtual", "earthcache", "earth cache", "webcam"])


def parse_gpx_file(file_path: str) -> List[WaypointItem]:
    """
    Parse caches and coordinates from a GPX file.
    Recognizes primary cache waypoints (<wpt> with GC...) and child final waypoints (FN..., FZ...).
    Supports exports from GSAK, Geoget, Garmin, UNI, FULL, and Cachly.
    """
    waypoints_map: Dict[str, WaypointItem] = {}
    child_finals: Dict[str, WaypointItem] = {}
    gc_metadata: Dict[str, Dict[str, str]] = {}

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        _strip_namespaces(root)
    except Exception:
        # Fallback to regex if XML is malformed
        return _parse_gpx_fallback_regex(file_path)

    base_filename = os.path.basename(file_path)

    for wpt in root.findall("wpt"):
        lat_str = wpt.attrib.get("lat", "").strip()
        lon_str = wpt.attrib.get("lon", "").strip()
        lat: Optional[float] = None
        lon: Optional[float] = None
        if lat_str and lon_str:
            try:
                lat = float(lat_str)
                lon = float(lon_str)
            except ValueError:
                pass

        name_elem = wpt.find("name")
        desc_elem = wpt.find("desc")
        type_elem = wpt.find("type")

        name = name_elem.text.strip() if name_elem is not None and name_elem.text else ""
        desc = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""
        wpt_type = type_elem.text.strip() if type_elem is not None and type_elem.text else ""

        # Extract best cache title: Groundspeak cache name -> urlname -> desc -> name
        cache_name = ""
        c_elem = wpt.find(".//cache")
        if c_elem is not None:
            c_name_elem = c_elem.find("name")
            if c_name_elem is not None and c_name_elem.text:
                cache_name = c_name_elem.text.strip()
            if not wpt_type:
                c_type_elem = c_elem.find("type")
                if c_type_elem is not None and c_type_elem.text:
                    wpt_type = c_type_elem.text.strip()
        if not cache_name:
            urlname_elem = wpt.find("urlname")
            if urlname_elem is not None and urlname_elem.text:
                cache_name = urlname_elem.text.strip()
        if not cache_name:
            cache_name = desc or name

        # Check if GPX contains a GSAK corrected coordinate hint or Cachly final indication
        has_modified_hint = False
        lat_before = wpt.find(".//LatBeforeCorrect")
        if lat_before is not None:
            has_modified_hint = True
        elif desc.strip().upper().startswith("FINAL"):
            has_modified_hint = True

        upper_name = name.upper()

        # 1. Main cache with GC code prefix (GC...)
        if upper_name.startswith("GC"):
            match = re.match(r"^(GC[A-Z0-9]+)", upper_name)
            if match:
                code = match.group(1)
                gc_metadata[code] = {"name": cache_name, "cache_type": wpt_type}
                if lat is not None and lon is not None:
                    waypoints_map[code] = WaypointItem(
                        gccode=code,
                        lat=lat,
                        lon=lon,
                        name=cache_name,
                        cache_type=wpt_type,
                        is_modified_hint=has_modified_hint,
                        source_file=base_filename
                    )

        # 2. Child waypoint of type Final (FN..., FZ... or Final Location)
        elif upper_name.startswith("FN") or upper_name.startswith("FZ") or "FINAL" in wpt_type.upper():
            derived_gc = None
            match = re.match(r"^(?:FN|FZ)([A-Z0-9]+)", upper_name)
            if match:
                derived_gc = "GC" + match.group(1)
            else:
                # Check <url> or <cmt> for parent GC code (e.g. Geoget format)
                url_elem = wpt.find("url")
                if url_elem is not None and url_elem.text:
                    m_url = re.search(r"(GC[A-Z0-9]+)", url_elem.text.upper())
                    if m_url:
                        derived_gc = m_url.group(1)

            if derived_gc and lat is not None and lon is not None:
                meta = gc_metadata.get(derived_gc, {})
                title = meta.get("name") or cache_name
                ctype = meta.get("cache_type") or "Final Location"
                child_finals[derived_gc] = WaypointItem(
                    gccode=derived_gc,
                    lat=lat,
                    lon=lon,
                    name=title,
                    cache_type=ctype,
                    is_modified_hint=True,
                    source_file=base_filename
                )

    # If a final child waypoint exists, prefer its coordinates and flag as modified
    for gc_code, final_item in child_finals.items():
        if gc_code in waypoints_map:
            waypoints_map[gc_code].lat = final_item.lat
            waypoints_map[gc_code].lon = final_item.lon
            waypoints_map[gc_code].is_modified_hint = True
            if not waypoints_map[gc_code].name or waypoints_map[gc_code].name == gc_code:
                waypoints_map[gc_code].name = final_item.name
        else:
            waypoints_map[gc_code] = final_item

    # Filter out items that have no valid coordinates
    return [
        item for item in waypoints_map.values()
        if item.lat is not None and item.lon is not None and not (item.lat == 0.0 and item.lon == 0.0)
    ]


def _parse_gpx_fallback_regex(file_path: str) -> List[WaypointItem]:
    """Fallback regex parser for malformed XML files."""
    items = []
    base_filename = os.path.basename(file_path)
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        pattern = re.compile(
            r'<wpt[^>]+lat=["\']([^"\']+)["\'][^>]+lon=["\']([^"\']+)["\'][^>]*>.*?<name>\s*(GC[A-Z0-9]+)\s*</name>',
            re.DOTALL | re.IGNORECASE
        )
        for lat_s, lon_s, code in pattern.findall(content):
            try:
                items.append(WaypointItem(
                    gccode=code.upper(),
                    lat=float(lat_s),
                    lon=float(lon_s),
                    name=code.upper(),
                    source_file=base_filename
                ))
            except ValueError:
                continue
    except Exception:
        pass
    return items


def parse_loc_file(file_path: str) -> List[WaypointItem]:
    """Parse caches and coordinates from legacy .loc XML format."""
    items = []
    base_filename = os.path.basename(file_path)
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        pattern = re.compile(
            r'<waypoint>.*?<name\s+id=["\'](GC[A-Z0-9]+)["\'].*?<coord\s+lat=["\']([^"\']+)["\']\s+lon=["\']([^"\']+)["\']',
            re.DOTALL | re.IGNORECASE
        )
        for code, lat_s, lon_s in pattern.findall(content):
            try:
                items.append(WaypointItem(
                    gccode=code.upper(),
                    lat=float(lat_s),
                    lon=float(lon_s),
                    name=code.upper(),
                    source_file=base_filename
                ))
            except ValueError:
                continue
    except Exception:
        pass
    return items


def parse_file(file_path: str) -> List[WaypointItem]:
    """Parse a single waypoint file (.gpx or .loc)."""
    if not os.path.isfile(file_path):
        return []
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".gpx":
        return parse_gpx_file(file_path)
    elif ext == ".loc":
        return parse_loc_file(file_path)
    return []


def parse_folder(folder_path: str) -> List[WaypointItem]:
    """Recursively search a folder and parse all .gpx and .loc files."""
    all_items: Dict[str, WaypointItem] = {}
    if not os.path.isdir(folder_path):
        return []

    for root, _, files in os.walk(folder_path):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in [".gpx", ".loc"]:
                full_path = os.path.join(root, file)
                for item in parse_file(full_path):
                    if item.gccode not in all_items or item.is_modified_hint:
                        all_items[item.gccode] = item

    return list(all_items.values())


def extract_gc_codes_from_string(text: str) -> List[str]:
    """Extract unique GC codes from arbitrary text (comma, space, or newline separated)."""
    found = re.findall(r"\bGC[A-Z0-9]+\b", text.upper())
    return list(dict.fromkeys(found))
