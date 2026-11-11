#!/usr/bin/env python3
"""Build a compact local Fortaleza basemap from a Geofabrik OSM PBF.

The output is GeoJSON with the municipality boundary, land cover, major roads
and a curated local-street network. It is intended as a same-origin Leaflet
fallback when the detailed raster tile service is unavailable.

Requires GDAL/ogr2ogr. The generated file is a derived OSM database under
ODbL 1.0 and must retain OpenStreetMap attribution in the map UI.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "fortaleza-basemap.json"
DEFAULT_SOURCE_URL = (
    "https://download.geofabrik.de/south-america/brazil/nordeste-latest.osm.pbf"
)
HIGHWAYS = (
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
)
LOCAL_HIGHWAYS = ("residential", "unclassified", "living_street")
LANDCOVER_CLASSES = ("water", "green", "sports", "beach", "cemetery")
LOCAL_MIN_LENGTH_DEGREES = 0.008
EXPECTED_ENVELOPE = (-38.65, -3.91, -38.38, -3.67)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pbf", type=Path, required=True, help="Path to a Geofabrik Nordeste .osm.pbf"
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        required=True,
        help="GeoJSON polygon for the Fortaleza municipality boundary",
    )
    parser.add_argument("--snapshot-date", required=True, help="PBF snapshot date (YYYY-MM-DD)")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=900_000,
        help="Fail instead of writing when the output exceeds this size",
    )
    return parser.parse_args()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def load_geojson(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection" or not isinstance(
        payload.get("features"), list
    ):
        raise RuntimeError(f"Expected a GeoJSON FeatureCollection: {path}")
    return payload


def line_strings(geometry: dict[str, Any] | None) -> list[list[list[float]]]:
    """Return only line coordinates, dropping points emitted by ST_Union."""
    if not geometry:
        return []
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        return [geometry["coordinates"]]
    if geometry_type == "MultiLineString":
        return geometry["coordinates"]
    if geometry_type == "GeometryCollection":
        result: list[list[list[float]]] = []
        for child in geometry.get("geometries", []):
            result.extend(line_strings(child))
        return result
    return []


def polygon_rings(geometry: dict[str, Any] | None) -> list[list[list[list[float]]]]:
    """Normalize Polygon, MultiPolygon or polygonal GeometryCollection."""
    if not geometry:
        return []
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        return [geometry["coordinates"]]
    if geometry_type == "MultiPolygon":
        return geometry["coordinates"]
    if geometry_type == "GeometryCollection":
        result: list[list[list[list[float]]]] = []
        for child in geometry.get("geometries", []):
            result.extend(polygon_rings(child))
        return result
    return []


def boundary_geometry(payload: dict[str, Any]) -> dict[str, Any]:
    polygons: list[list[list[list[float]]]] = []
    for feature in payload["features"]:
        polygons.extend(polygon_rings(feature.get("geometry")))
    if not polygons:
        raise RuntimeError("The boundary contains no polygon geometry.")
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def road_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    features = []
    for feature in payload["features"]:
        highway = feature.get("properties", {}).get("highway")
        lines = line_strings(feature.get("geometry"))
        if highway in HIGHWAYS and lines:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"kind": "road", "highway": highway},
                    "geometry": {"type": "MultiLineString", "coordinates": lines},
                }
            )
    features.sort(key=lambda feature: HIGHWAYS.index(feature["properties"]["highway"]))
    if not features:
        raise RuntimeError("No major roads were extracted from the PBF.")
    return features


def line_length(line: list[list[float]]) -> float:
    return sum(
        math.hypot(end[0] - start[0], end[1] - start[1])
        for start, end in zip(line, line[1:])
    )


def local_road_feature(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    groups: dict[str, list[list[list[float]]]] = {}
    for feature in payload["features"]:
        properties = feature.get("properties", {})
        name = properties.get("name")
        highway = properties.get("highway")
        if not isinstance(name, str) or not name.strip() or highway not in LOCAL_HIGHWAYS:
            continue
        groups.setdefault(name.strip(), []).extend(line_strings(feature.get("geometry")))

    selected: list[list[list[float]]] = []
    selected_names = 0
    for name in sorted(groups, key=str.casefold):
        lines = groups[name]
        if sum(line_length(line) for line in lines) < LOCAL_MIN_LENGTH_DEGREES:
            continue
        selected.extend(lines)
        selected_names += 1
    if not selected:
        raise RuntimeError("No local street network was extracted from the PBF.")
    return (
        {
            "type": "Feature",
            "properties": {"kind": "road", "highway": "local"},
            "geometry": {"type": "MultiLineString", "coordinates": selected},
        },
        selected_names,
    )


def landcover_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    features = []
    for feature in payload["features"]:
        land_class = feature.get("properties", {}).get("kind")
        polygons = polygon_rings(feature.get("geometry"))
        if land_class not in LANDCOVER_CLASSES or not polygons:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "landcover", "class": land_class},
                "geometry": {"type": "MultiPolygon", "coordinates": polygons},
            }
        )
    features.sort(key=lambda feature: LANDCOVER_CLASSES.index(feature["properties"]["class"]))
    if len(features) != len(LANDCOVER_CLASSES):
        raise RuntimeError(f"Expected {len(LANDCOVER_CLASSES)} land-cover classes, got {len(features)}.")
    return features


def positions(value: Any):
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        yield float(value[0]), float(value[1])
    elif isinstance(value, list):
        for item in value:
            yield from positions(item)


def validate_bounds(features: list[dict[str, Any]]) -> list[float]:
    coordinates = [
        point
        for feature in features
        for point in positions(feature.get("geometry", {}).get("coordinates"))
    ]
    if not coordinates:
        raise RuntimeError("The basemap contains no coordinates.")
    xs, ys = zip(*coordinates)
    bounds = [min(xs), min(ys), max(xs), max(ys)]
    west, south, east, north = EXPECTED_ENVELOPE
    if bounds[0] < west or bounds[1] < south or bounds[2] > east or bounds[3] > north:
        raise RuntimeError(f"Basemap bounds are outside Fortaleza: {bounds}")
    if bounds[0] > -38.62 or bounds[1] > -3.88 or bounds[2] < -38.42 or bounds[3] < -3.71:
        raise RuntimeError(f"Basemap coverage is unexpectedly small: {bounds}")
    return [round(value, 5) for value in bounds]


def build(args: argparse.Namespace) -> bytes:
    highway_filter = ",".join(f"'{value}'" for value in HIGHWAYS)
    local_filter = ",".join(f"'{value}'" for value in LOCAL_HIGHWAYS)
    landcover_filter = (
        "natural IN ('water','wood','scrub','grassland','wetland','beach') OR "
        "landuse IN ('reservoir','basin','forest','grass','meadow','recreation_ground',"
        "'cemetery','village_green') OR "
        "leisure IN ('park','nature_reserve','garden','recreation_ground','pitch','golf_course')"
    )
    landcover_sql = (
        "SELECT kind, ST_Union(fixed) AS geometry FROM ( "
        "SELECT CASE "
        "WHEN natural='water' OR landuse IN ('reservoir','basin') THEN 'water' "
        "WHEN natural='beach' THEN 'beach' "
        "WHEN landuse='cemetery' THEN 'cemetery' "
        "WHEN leisure IN ('pitch','golf_course') THEN 'sports' ELSE 'green' END AS kind, "
        "ST_Buffer(geometry,0) AS fixed FROM multipolygons "
        "WHERE ST_GeometryType(geometry) IN ('POLYGON','MULTIPOLYGON') ) "
        "WHERE (kind IN ('water','beach','cemetery') AND ST_Area(fixed) > 0.00000008) "
        "OR (kind='sports' AND ST_Area(fixed) > 0.00000025) "
        "OR (kind='green' AND ST_Area(fixed) > 0.00000035) GROUP BY kind"
    )
    with tempfile.TemporaryDirectory(prefix="to-com-fome-fortaleza-ia-basemap-") as directory:
        temp = Path(directory)
        clipped = temp / "roads-clipped.geojson"
        dissolved = temp / "roads-dissolved.geojson"
        local_roads = temp / "local-roads.geojson"
        landcover_raw = temp / "landcover-raw.geojson"
        landcover_filtered = temp / "landcover-filtered.geojson"
        simplified_boundary = temp / "boundary.geojson"

        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(clipped),
                str(args.pbf),
                "lines",
                "-nln",
                "roads",
                "-spat",
                "-38.66",
                "-3.92",
                "-38.38",
                "-3.66",
                "-clipsrc",
                str(args.boundary),
                "-where",
                f"highway IN ({highway_filter})",
                "-select",
                "highway",
                "-simplify",
                "0.00004",
                "-lco",
                "RFC7946=YES",
                "-lco",
                "COORDINATE_PRECISION=5",
            ]
        )
        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(local_roads),
                str(args.pbf),
                "lines",
                "-spat",
                "-38.66",
                "-3.92",
                "-38.38",
                "-3.66",
                "-clipsrc",
                str(args.boundary),
                "-where",
                f"name IS NOT NULL AND highway IN ({local_filter})",
                "-select",
                "highway,name",
                "-simplify",
                "0.00004",
                "-lco",
                "RFC7946=YES",
                "-lco",
                "COORDINATE_PRECISION=5",
            ]
        )
        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(landcover_raw),
                str(args.pbf),
                "multipolygons",
                "-spat",
                "-38.66",
                "-3.92",
                "-38.38",
                "-3.66",
                "-clipsrc",
                str(args.boundary),
                "-where",
                landcover_filter,
                "-select",
                "name,landuse,leisure,natural",
                "-simplify",
                "0.00004",
                "-lco",
                "RFC7946=YES",
                "-lco",
                "COORDINATE_PRECISION=5",
            ]
        )
        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(landcover_filtered),
                str(landcover_raw),
                "-dialect",
                "SQLite",
                "-sql",
                landcover_sql,
                "-simplify",
                "0.00010",
                "-lco",
                "RFC7946=YES",
                "-lco",
                "COORDINATE_PRECISION=5",
            ]
        )
        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(dissolved),
                str(clipped),
                "-dialect",
                "SQLite",
                "-sql",
                "SELECT highway, ST_LineMerge(ST_Union(geometry)) AS geometry "
                "FROM roads WHERE ST_GeometryType(geometry) IN "
                "('LINESTRING','MULTILINESTRING') GROUP BY highway",
                "-simplify",
                "0.00008",
                "-lco",
                "RFC7946=YES",
                "-lco",
                "COORDINATE_PRECISION=5",
            ]
        )
        run(
            [
                "ogr2ogr",
                "-f",
                "GeoJSON",
                str(simplified_boundary),
                str(args.boundary),
                "-nln",
                "boundary",
                "-select",
                "name",
                "-simplify",
                "0.00008",
                "-makevalid",
                "-lco",
                "RFC7946=YES",
                "-lco",
                "COORDINATE_PRECISION=5",
            ]
        )

        roads = road_features(load_geojson(dissolved))
        local_road, local_street_groups = local_road_feature(load_geojson(local_roads))
        landcover = landcover_features(load_geojson(landcover_filtered))
        city = boundary_geometry(load_geojson(simplified_boundary))

    features = [
        {
            "type": "Feature",
            "properties": {"kind": "boundary", "name": "Fortaleza"},
            "geometry": city,
        },
        *landcover,
        *roads,
        local_road,
    ]
    bounds = validate_bounds(features)
    road_classes = [feature["properties"]["highway"] for feature in roads] + ["local"]
    payload = {
        "type": "FeatureCollection",
        "meta": {
            "title": "Mapa vetorial local de Fortaleza",
            "snapshot_date": args.snapshot_date,
            "coverage": "Limite municipal, cobertura do solo e rede viária de Fortaleza.",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors",
            "source_url": args.source_url,
            "road_classes": road_classes,
            "landcover_classes": list(LANDCOVER_CLASSES),
            "local_street_groups": local_street_groups,
            "feature_count": len(features),
            "bbox": bounds,
            "coordinate_precision": 5,
            "simplification_degrees": {
                "major_roads": 0.00008,
                "local_roads": 0.00004,
                "landcover": 0.00010,
            },
        },
        "features": features,
    }
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def main() -> None:
    args = parse_args()
    if shutil.which("ogr2ogr") is None:
        raise SystemExit("ogr2ogr not found; install the GDAL command-line tools.")
    if not args.pbf.is_file():
        raise SystemExit(f"PBF not found: {args.pbf}")
    if not args.boundary.is_file():
        raise SystemExit(f"Boundary not found: {args.boundary}")
    try:
        date.fromisoformat(args.snapshot_date)
    except ValueError as error:
        raise SystemExit("--snapshot-date must use YYYY-MM-DD") from error
    if args.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive")

    encoded = build(args)
    if len(encoded) > args.max_bytes:
        raise SystemExit(
            f"Basemap is {len(encoded):,} bytes, above the {args.max_bytes:,}-byte limit."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = args.output.with_suffix(args.output.suffix + ".tmp")
    pending.write_bytes(encoded)
    pending.replace(args.output)
    print(f"Saved {len(encoded):,}-byte Fortaleza basemap to {args.output}")


if __name__ == "__main__":
    main()
