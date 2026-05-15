#!/usr/bin/env python3
"""Build a property research brief from just an address.

All data sources are free and keyless:
  * Nominatim (OpenStreetMap) — geocoding
  * US Census Geocoder — GEOID / tract ID for US addresses
  * US Census ACS 5-year — tract demographics (up to 500 calls/day without key)
  * FEMA NFHL REST — flood hazard zone
  * OSM Overpass API — nearby POIs within a radius

Usage:
    research_property.py "300 S Carlton Ave, Wheaton, IL"
    research_property.py "111 E Van Emmon St, Yorkville, IL" --radius 1200
    research_property.py "<addr>" --out ~/EveBrain/.../brief.md

Output: Markdown brief under ~/EveBrain/04-Resources/Property-Research/ by default.
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time

import requests

USER_AGENT = "Eve-L&R/1.0 (property-research; alexander.reich@labrasseurandreich.com)"
DEFAULT_OUT_DIR = pathlib.Path.home() / "EveBrain" / "04-Resources" / "Property-Research"

# POI categories to pull via Overpass API.
# key=tag-expression; value=human label.
POI_CATEGORIES: dict[str, str] = {
    '["amenity"~"^(school|university|college)$"]': "Schools & colleges",
    '["shop"="supermarket"]': "Supermarkets",
    '["amenity"="hospital"]': "Hospitals",
    '["amenity"~"^(restaurant|cafe|fast_food)$"]': "Restaurants & cafes",
    '["amenity"="bank"]': "Banks",
    '["highway"="bus_stop"]': "Bus stops",
    '["public_transport"="station"]': "Transit stations",
    '["amenity"="parking"]': "Parking",
}


def slugify(s: str, max_len: int = 60) -> str:
    return (re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "property")[:max_len]


# --- Nominatim (geocoding) --------------------------------------------------

def geocode_nominatim(address: str) -> dict | None:
    url = "https://nominatim.openstreetmap.org/search"
    r = requests.get(
        url,
        params={"q": address, "format": "json", "addressdetails": 1, "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    hit = data[0]
    return {
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
        "display_name": hit["display_name"],
        "address": hit.get("address", {}),
        "osm_type": hit.get("osm_type"),
        "osm_id": hit.get("osm_id"),
    }


# --- Census Geocoder + ACS --------------------------------------------------

def census_geocode(address: str) -> dict | None:
    url = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
    r = requests.get(
        url,
        params={
            "address": address,
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "layers": "Census Tracts",
            "format": "json",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    matches = r.json().get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    m = matches[0]
    geos = m.get("geographies", {}).get("Census Tracts", [])
    if not geos:
        return None
    g = geos[0]
    return {
        "matched_address": m.get("matchedAddress"),
        "state_fips": g.get("STATE"),
        "county_fips": g.get("COUNTY"),
        "tract": g.get("TRACT"),
        "geoid": g.get("GEOID"),
        "county_name": g.get("BASENAME"),
    }


# Census ACS 5-year variables we care about.
ACS_VARS = {
    "B01003_001E": "Total population",
    "B19013_001E": "Median household income ($)",
    "B25077_001E": "Median home value ($)",
    "B25064_001E": "Median gross rent ($/mo)",
    "B23025_005E": "Unemployed (labor force)",
    "B23025_002E": "In labor force",
    "B15003_022E": "Bachelor's degree holders (age 25+)",
    "B01002_001E": "Median age",
}


def fetch_acs(geo: dict) -> dict[str, str | None]:
    if not geo:
        return {}
    year = 2022
    get = ",".join(ACS_VARS.keys())
    url = f"https://api.census.gov/data/{year}/acs/acs5"
    params = {
        "get": get,
        "for": f"tract:{geo['tract']}",
        "in": f"state:{geo['state_fips']} county:{geo['county_fips']}",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    rows = r.json()
    if len(rows) < 2:
        return {}
    header, values = rows[0], rows[1]
    out: dict[str, str | None] = {}
    for var, label in ACS_VARS.items():
        idx = header.index(var)
        v = values[idx]
        # ACS sentinels for missing data
        if v in ("-666666666", "-888888888", "-999999999", None):
            out[label] = None
        else:
            out[label] = v
    return out


# --- FEMA NFHL --------------------------------------------------------------

def fetch_fema_flood(lat: float, lon: float) -> dict | None:
    # Layer 28 = Flood Hazard Zones
    url = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
    r = requests.get(
        url,
        params={
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=30,
    )
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        return None
    attrs = feats[0].get("attributes", {})
    return {
        "zone": attrs.get("FLD_ZONE"),
        "subtype": attrs.get("ZONE_SUBTY"),
        "sfha": attrs.get("SFHA_TF"),  # "T" = Special Flood Hazard Area
        "bfe": attrs.get("STATIC_BFE"),
    }


# --- Overpass (nearby POIs) -------------------------------------------------

def fetch_overpass_pois(lat: float, lon: float, radius_m: int) -> dict[str, list[dict]]:
    # Build a compound Overpass query hitting all categories at once.
    selectors = "\n".join(
        f'  node{tag}(around:{radius_m},{lat},{lon});'
        for tag in POI_CATEGORIES
    )
    query = f"""
[out:json][timeout:30];
(
{selectors}
);
out center tags 80;
""".strip()
    r = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=45,
    )
    r.raise_for_status()
    elements = r.json().get("elements", [])

    buckets: dict[str, list[dict]] = {label: [] for label in POI_CATEGORIES.values()}
    for el in elements:
        tags = el.get("tags", {})
        for tag_expr, label in POI_CATEGORIES.items():
            if _matches_tag(tag_expr, tags):
                buckets[label].append({
                    "name": tags.get("name") or "(unnamed)",
                    "lat": el.get("lat") or el.get("center", {}).get("lat"),
                    "lon": el.get("lon") or el.get("center", {}).get("lon"),
                })
                break
    return buckets


def _matches_tag(expr: str, tags: dict) -> bool:
    # Very small parser for expressions like ["amenity"="school"] or ["amenity"~"^(school|...)$"].
    m = re.match(r'\["([^"]+)"(=|~)"([^"]+)"\]', expr)
    if not m:
        return False
    key, op, val = m.groups()
    actual = tags.get(key)
    if actual is None:
        return False
    if op == "=":
        return actual == val
    return re.search(val, actual) is not None


# --- Output writer ----------------------------------------------------------

def fmt_money(v: str | None) -> str:
    if not v:
        return "—"
    try:
        n = int(float(v))
        return f"${n:,}"
    except (ValueError, TypeError):
        return str(v)


def fmt_count(v: str | None) -> str:
    if not v:
        return "—"
    try:
        return f"{int(float(v)):,}"
    except (ValueError, TypeError):
        return str(v)


def build_markdown(address: str, geo_nom: dict | None, geo_census: dict | None,
                   acs: dict, fema: dict | None, pois: dict[str, list[dict]],
                   radius_m: int) -> str:
    today = dt.date.today().isoformat()
    coords = f"{geo_nom['lat']:.6f}, {geo_nom['lon']:.6f}" if geo_nom else "—"
    map_link = (
        f"https://www.openstreetmap.org/?mlat={geo_nom['lat']}&mlon={geo_nom['lon']}#map=18/{geo_nom['lat']}/{geo_nom['lon']}"
        if geo_nom else ""
    )
    gmaps_link = (
        f"https://www.google.com/maps/search/?api=1&query={geo_nom['lat']},{geo_nom['lon']}"
        if geo_nom else ""
    )

    lines: list[str] = []
    lines.append("---")
    lines.append("type: property-research")
    lines.append(f"date: {today}")
    lines.append(f"input-address: {address!r}")
    lines.append(f"matched-address: {(geo_nom and geo_nom['display_name']) or ''!r}")
    if geo_nom:
        lines.append(f"lat: {geo_nom['lat']}")
        lines.append(f"lon: {geo_nom['lon']}")
    if geo_census:
        lines.append(f"census-geoid: {geo_census.get('geoid') or ''}")
    lines.append("tags: [property-research]")
    lines.append("---")
    lines.append("")
    lines.append(f"# Property Research — {address}")
    lines.append("")

    # Location
    lines.append("## Location")
    if geo_nom:
        lines.append(f"- **Matched address:** {geo_nom['display_name']}")
        lines.append(f"- **Coordinates:** {coords}")
        lines.append(f"- **Map:** [OpenStreetMap]({map_link}) · [Google Maps]({gmaps_link})")
    else:
        lines.append("- [TODO] Address did not resolve via Nominatim — verify spelling / try a more specific form.")
    lines.append("")

    # Census
    lines.append("## Demographics (US Census ACS 5-year, 2022, census tract)")
    if geo_census:
        lines.append(f"- **Tract GEOID:** {geo_census.get('geoid')}")
        lines.append(f"- **County:** {geo_census.get('county_name')}")
        if acs:
            lines.append(f"- **Population:** {fmt_count(acs.get('Total population'))}")
            lines.append(f"- **Median household income:** {fmt_money(acs.get('Median household income ($)'))}")
            lines.append(f"- **Median home value:** {fmt_money(acs.get('Median home value ($)'))}")
            lines.append(f"- **Median gross rent:** {fmt_money(acs.get('Median gross rent ($/mo)'))}/mo")
            lines.append(f"- **Median age:** {acs.get('Median age') or '—'}")
            lines.append(f"- **In labor force:** {fmt_count(acs.get('In labor force'))}")
            bachelors_key = "Bachelor's degree holders (age 25+)"
            lines.append(f"- **Bachelor's degree holders (25+):** {fmt_count(acs.get(bachelors_key))}")
        else:
            lines.append("- [TODO] ACS pull returned no rows.")
    else:
        lines.append("- [TODO] US Census Geocoder did not match this address — may be non-US or a new subdivision.")
    lines.append("")

    # FEMA
    lines.append("## Flood Risk (FEMA NFHL)")
    if fema and fema.get("zone"):
        sfha = "YES (Special Flood Hazard Area)" if fema.get("sfha") == "T" else "No"
        lines.append(f"- **Flood zone:** {fema['zone']}" + (f" ({fema['subtype']})" if fema.get("subtype") else ""))
        lines.append(f"- **SFHA:** {sfha}")
        if fema.get("bfe") and fema["bfe"] not in (0, -9999):
            lines.append(f"- **Base Flood Elevation:** {fema['bfe']} ft")
    else:
        lines.append("- **Flood zone:** X or no overlap with NFHL data (minimal flood hazard indicated).")
    lines.append("")

    # POIs
    lines.append(f"## Nearby POIs (within {radius_m} m via OpenStreetMap)")
    for label, items in pois.items():
        if not items:
            continue
        lines.append(f"### {label} ({len(items)})")
        for p in items[:12]:
            lines.append(f"- {p['name']}")
        if len(items) > 12:
            lines.append(f"- _+{len(items) - 12} more_")
        lines.append("")

    lines.append("## Manual Review")
    lines.append("- [TODO] Zoning & permitted uses (local municipality)")
    lines.append("- [TODO] County assessor: parcel ID, assessed value, property tax history")
    lines.append("- [TODO] Environmental: Phase I ESA, underground storage tanks")
    lines.append("- [TODO] Traffic counts (IDOT / local ADT)")
    lines.append("- [TODO] Comparable sales + cap rate benchmarks")
    lines.append("- [TODO] Market commentary: vacancy, absorption, tenant demand")
    lines.append("")

    return "\n".join(lines) + "\n"


def build_output_path(address: str, explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    return DEFAULT_OUT_DIR / f"{today}_{slugify(address)}.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a property research brief from an address.")
    ap.add_argument("address", help="Street address (US addresses get the most data).")
    ap.add_argument("--out", help="Explicit output Markdown path.")
    ap.add_argument("--radius", type=int, default=800, help="POI search radius in meters (default 800).")
    ap.add_argument("--skip-poi", action="store_true", help="Skip Overpass POI lookup (faster).")
    args = ap.parse_args()

    print(f"# [1/4] Geocoding via Nominatim…", file=sys.stderr)
    geo_nom = geocode_nominatim(args.address)
    # Nominatim asks for ≥1s between requests per their usage policy.
    time.sleep(1)

    print(f"# [2/4] Resolving Census tract…", file=sys.stderr)
    geo_census = None
    acs: dict = {}
    try:
        geo_census = census_geocode(args.address)
        if geo_census:
            acs = fetch_acs(geo_census)
    except requests.RequestException as e:
        print(f"#   census lookup failed: {e}", file=sys.stderr)

    print(f"# [3/4] FEMA flood zone…", file=sys.stderr)
    fema = None
    if geo_nom:
        try:
            fema = fetch_fema_flood(geo_nom["lat"], geo_nom["lon"])
        except requests.RequestException as e:
            print(f"#   FEMA lookup failed: {e}", file=sys.stderr)

    pois: dict[str, list[dict]] = {}
    if not args.skip_poi and geo_nom:
        print(f"# [4/4] Nearby POIs via Overpass (r={args.radius}m)…", file=sys.stderr)
        try:
            pois = fetch_overpass_pois(geo_nom["lat"], geo_nom["lon"], args.radius)
        except requests.RequestException as e:
            print(f"#   Overpass lookup failed: {e}", file=sys.stderr)

    md = build_markdown(args.address, geo_nom, geo_census, acs, fema, pois, args.radius)
    out = build_output_path(args.address, args.out)
    out.write_text(md, encoding="utf-8")

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
