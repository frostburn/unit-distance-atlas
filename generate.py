#!/usr/bin/env python3
"""Generate and maintain a local atlas of dense planar unit-distance graphs.

The search works inside exact cyclotomic host graphs.  A host vertex is an
integer coefficient vector in the power basis of Q(zeta_m); two host vertices
are adjacent exactly when their difference is an m-th root of unity.  Floating
point is used only for the planar embedding written to SVG/JSON.

A run searches all sizes in one pass per restart by randomized low-degree
peeling.  Existing records are read from data/catalog.local.json (falling back
to the published data/catalog.json) and an SVG/metadata
pair is replaced only when the new graph has strictly more unit-distance edges.

Typical use:

    pip install sympy
    python generate.py --max-n 250 --restarts 8
    python serve.py

A later run with more restarts is incremental:

    python generate.py --max-n 250 --restarts 64 --seed 12345

The filename width is permanently four digits, anticipating n <= 9999.
"""

from __future__ import annotations

import argparse
from array import array
import cmath
import heapq
import itertools
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    import sympy as sp
except ImportError as exc:  # pragma: no cover - friendly CLI failure
    raise SystemExit("SymPy is required: pip install sympy") from exc

MAX_N = 9_999
PAD_WIDTH = 4
SCHEMA_VERSION = 1
GENERATOR_VERSION = "3.0"
X = sp.Symbol("x")
Vec = tuple[int, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: object, *, pretty: bool = False) -> None:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    atomic_write_text(path, text)


def add(a: Vec, b: Vec) -> Vec:
    return tuple(x + y for x, y in zip(a, b))


def neg(a: Vec) -> Vec:
    return tuple(-x for x in a)


def product(values: Sequence[int]) -> int:
    answer = 1
    for value in values:
        answer *= value
    return answer


def balanced_lengths(dimension: int, target: int, minimum: int = 2) -> tuple[int, ...]:
    """Return a nearly cubical integer box with volume at least ``target``."""
    lengths = [minimum] * dimension
    volume = product(lengths)
    cursor = 0

    while volume < target:
        smallest = min(lengths)
        choices = [i for i, length in enumerate(lengths) if length == smallest]
        index = choices[cursor % len(choices)]
        cursor += 1
        old = lengths[index]
        lengths[index] += 1
        volume = volume // old * lengths[index]

    # Interleave longer and shorter axes rather than placing all long axes first.
    ordered = sorted(lengths, reverse=True)
    result = [0] * dimension
    slots = list(range(0, dimension, 2)) + list(range(1, dimension, 2))
    for slot, length in zip(slots, ordered):
        result[slot] = length
    return tuple(result)


@dataclass(frozen=True)
class HostSpec:
    key: str
    label: str
    kind: str
    m: int
    lengths: tuple[int, ...] = ()
    active_coordinates: tuple[int, ...] = ()

    @property
    def size(self) -> int:
        if self.kind == "box":
            return product(self.lengths)
        if self.kind == "binary":
            return 1 << len(self.active_coordinates)
        raise ValueError(f"unknown host kind {self.kind!r}")

    def metadata(self, basis_dimension: int) -> dict[str, object]:
        result: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "cyclotomicOrder": self.m,
            "basisDimension": basis_dimension,
            "hostVertices": self.size,
        }
        if self.lengths:
            result["lengths"] = list(self.lengths)
        if self.active_coordinates:
            result["activeCoordinates"] = list(self.active_coordinates)
        return result


@dataclass
class Host:
    spec: HostSpec
    points: list[Vec]
    steps: list[Vec]
    neighbors: list[list[int]]
    basis_dimension: int


@dataclass(eq=False)
class PeelRun:
    spec: HostSpec
    removal_order: array
    last_vertex: int
    restart: int
    run_seed: int
    choice_pool: int
    degree_slack: int


@dataclass
class StoredRecord:
    n: int
    edges: int
    summary: dict[str, object]


def cyclotomic_steps(m: int) -> tuple[int, list[Vec]]:
    polynomial = sp.Poly(sp.cyclotomic_poly(m, X), X, domain=sp.ZZ)
    dimension = polynomial.degree()
    steps: list[Vec] = []

    for power in range(m):
        remainder = sp.rem(sp.Poly(X**power, X, domain=sp.ZZ), polynomial)
        steps.append(tuple(int(remainder.nth(i)) for i in range(dimension)))

    steps = list(dict.fromkeys(steps))
    step_set = set(steps)
    if any(neg(step) not in step_set for step in steps):
        raise ValueError(f"m={m} produced a non-symmetric root step set")
    return dimension, steps


def points_for_spec(spec: HostSpec, dimension: int) -> list[Vec]:
    if spec.kind == "box":
        if len(spec.lengths) != dimension:
            raise ValueError(
                f"{spec.key}: m={spec.m} has degree {dimension}, "
                f"but {len(spec.lengths)} box lengths were supplied"
            )
        offsets = [-(length // 2) for length in spec.lengths]
        ranges = [range(offset, offset + length) for offset, length in zip(offsets, spec.lengths)]
        return list(itertools.product(*ranges))

    if spec.kind == "binary":
        if any(i < 0 or i >= dimension for i in spec.active_coordinates):
            raise ValueError(f"{spec.key}: active coordinate outside the power basis")
        points: list[Vec] = []
        for bits in itertools.product((0, 1), repeat=len(spec.active_coordinates)):
            vector = [0] * dimension
            for coordinate, bit in zip(spec.active_coordinates, bits):
                vector[coordinate] = bit
            points.append(tuple(vector))
        return points

    raise ValueError(f"unknown host kind {spec.kind!r}")


def build_host(spec: HostSpec) -> Host:
    dimension, steps = cyclotomic_steps(spec.m)
    points = points_for_spec(spec, dimension)
    index = {point: i for i, point in enumerate(points)}
    neighbors: list[list[int]] = [[] for _ in points]

    for i, point in enumerate(points):
        row = neighbors[i]
        for step in steps:
            j = index.get(add(point, step))
            if j is not None:
                row.append(j)

    return Host(
        spec=spec,
        points=points,
        steps=steps,
        neighbors=neighbors,
        basis_dimension=dimension,
    )


def spread_coordinates(dimension: int, count: int) -> tuple[int, ...]:
    """Choose two separated coordinate blocks in a power basis."""
    if not 1 <= count <= dimension:
        raise ValueError("invalid number of active binary coordinates")
    left = (count + 1) // 2
    right = count - left
    coordinates = list(range(left))
    if right:
        coordinates.extend(range(dimension - right, dimension))
    return tuple(coordinates)


def host_specs(max_n: int, requested: set[str]) -> list[HostSpec]:
    available = {"hex", "z12", "z18", "z24", "z30", "binary"}
    unknown = requested - available
    if unknown:
        raise ValueError(f"unknown host name(s): {', '.join(sorted(unknown))}")

    specs: list[HostSpec] = []

    def add_box(name: str, m: int, factor: float, minimum: int = 2) -> None:
        dimension = int(sp.totient(m))
        target = max(max_n, math.ceil(max_n * factor))
        lengths = balanced_lengths(dimension, target, minimum)
        specs.append(
            HostSpec(
                key=f"{name}-{'x'.join(map(str, lengths))}",
                label=f"ζ{m} box {'×'.join(map(str, lengths))}",
                kind="box",
                m=m,
                lengths=lengths,
            )
        )

    if "hex" in requested:
        add_box("hex", 6, 1.08, minimum=1)
    if "z12" in requested:
        add_box("z12", 12, 1.12)
    if "z18" in requested:
        add_box("z18", 18, 1.16)
    if "z24" in requested:
        add_box("z24", 24, 1.20)
    if "z30" in requested:
        add_box("z30", 30, 1.20)
    if "binary" in requested:
        dimension = int(sp.totient(60))
        target = max(2, math.ceil(max_n * 1.20))
        count = max(1, math.ceil(math.log2(target)))
        count = min(count, dimension)
        active = spread_coordinates(dimension, count)
        specs.append(
            HostSpec(
                key=f"z60-binary-{count}",
                label=f"ζ60 binary {count}-cube",
                kind="binary",
                m=60,
                active_coordinates=active,
            )
        )

    if not specs:
        raise ValueError("at least one host must be selected")
    if any(spec.size < max_n for spec in specs):
        bad = [spec.key for spec in specs if spec.size < max_n]
        raise AssertionError(f"host construction undershot max_n: {bad}")
    return specs


def pop_valid(heap: list[tuple[int, float, int]], alive: bytearray, degrees: list[int]) -> tuple[int, float, int]:
    while heap:
        degree, tie, vertex = heapq.heappop(heap)
        if alive[vertex] and degrees[vertex] == degree:
            return degree, tie, vertex
    raise RuntimeError("peeling heap unexpectedly became empty")


def randomized_peel(
    host: Host,
    max_n: int,
    rng: random.Random,
    *,
    choice_pool: int,
    degree_slack: int,
) -> tuple[array, int, list[int]]:
    """Peel a host to one vertex and return order, survivor, and edge counts.

    ``edge_counts[n]`` is the number of edges in the nested n-vertex survivor
    whenever 1 <= n <= max_n.
    """
    size = len(host.points)
    alive = bytearray(b"\x01") * size
    degrees = [len(row) for row in host.neighbors]
    edges = sum(degrees) // 2
    alive_count = size
    edge_counts = [-1] * (max_n + 1)
    if alive_count <= max_n:
        edge_counts[alive_count] = edges

    heap: list[tuple[int, float, int]] = [
        (degree, rng.random(), vertex) for vertex, degree in enumerate(degrees)
    ]
    heapq.heapify(heap)
    removal_order = array("I")

    while alive_count > 1:
        first = pop_valid(heap, alive, degrees)
        minimum_degree = first[0]
        candidates = [first]

        while len(candidates) < choice_pool:
            try:
                candidate = pop_valid(heap, alive, degrees)
            except RuntimeError:
                break
            if candidate[0] > minimum_degree + degree_slack:
                heapq.heappush(heap, candidate)
                break
            candidates.append(candidate)

        # The exponential weighting admits some slack vertices without throwing
        # away the strong minimum-degree bias.
        weights = [math.exp(-1.35 * (item[0] - minimum_degree)) for item in candidates]
        chosen_index = rng.choices(range(len(candidates)), weights=weights, k=1)[0]
        _, _, vertex = candidates[chosen_index]

        for i, candidate in enumerate(candidates):
            if i != chosen_index:
                heapq.heappush(heap, candidate)

        alive[vertex] = 0
        removal_order.append(vertex)
        edges -= degrees[vertex]
        alive_count -= 1

        for neighbor in host.neighbors[vertex]:
            if alive[neighbor]:
                degrees[neighbor] -= 1
                heapq.heappush(heap, (degrees[neighbor], rng.random(), neighbor))

        if alive_count <= max_n:
            edge_counts[alive_count] = edges

    last_vertex = alive.index(1)
    if edge_counts[1] != 0:
        raise AssertionError("a one-vertex graph must have zero edges")
    return removal_order, last_vertex, edge_counts


def load_existing_records(data_dir: Path) -> dict[int, StoredRecord]:
    local_catalog_path = data_dir / "catalog.local.json"
    published_catalog_path = data_dir / "catalog.json"
    catalog_path = local_catalog_path if local_catalog_path.exists() else published_catalog_path
    records: dict[int, StoredRecord] = {}

    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            for summary in catalog.get("records", []):
                n = int(summary["n"])
                edges = int(summary["edges"])
                svg = data_dir.parent / str(summary.get("svg", ""))
                metadata = data_dir.parent / str(summary.get("metadata", ""))
                if svg.exists() and metadata.exists():
                    records[n] = StoredRecord(n=n, edges=edges, summary=dict(summary))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"warning: could not read {catalog_path}: {exc}", file=sys.stderr)

    if records:
        return records

    # Recovery path if catalog.json is missing or damaged.
    records_dir = data_dir / "records"
    for path in sorted(records_dir.glob("[0-9][0-9][0-9][0-9].json")):
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            n = int(metadata["n"])
            edges = int(metadata["edges"])
            filename = f"{n:0{PAD_WIDTH}d}"
            svg_path = data_dir / "graphs" / f"{filename}.svg"
            if not svg_path.exists():
                continue
            summary = summary_from_metadata(metadata)
            records[n] = StoredRecord(n=n, edges=edges, summary=summary)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"warning: skipped bad record {path}: {exc}", file=sys.stderr)
    return records


def complex_coordinates(points: Sequence[Vec], m: int) -> list[complex]:
    zeta = cmath.exp(2j * math.pi / m)
    powers = [zeta**i for i in range(len(points[0]))]
    return [sum(coefficient * power for coefficient, power in zip(point, powers)) for point in points]


def compact_number(value: float, digits: int = 6) -> str:
    if abs(value) < 0.5 * 10 ** (-digits):
        value = 0.0
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-0"} else "0"


def node_radius(n: int) -> float:
    if n <= 12:
        return 0.115
    if n <= 30:
        return 0.095
    if n <= 80:
        return 0.075
    if n <= 200:
        return 0.055
    if n <= 500:
        return 0.040
    if n <= 1_500:
        return 0.029
    if n <= 4_000:
        return 0.021
    return 0.016


def edge_width_px(n: int) -> float:
    if n <= 30:
        return 2.2
    if n <= 80:
        return 1.8
    if n <= 200:
        return 1.35
    if n <= 500:
        return 1.0
    if n <= 1_500:
        return 0.75
    if n <= 4_000:
        return 0.55
    return 0.42


def render_svg(coords: Sequence[complex], edges: Sequence[tuple[int, int]]) -> str:
    n = len(coords)
    xs = [point.real for point in coords]
    ys = [-point.imag for point in coords]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    span = max(xmax - xmin, ymax - ymin, 1.0)
    padding = max(0.30, 0.065 * span)
    view_x = xmin - padding
    view_y = ymin - padding
    view_width = max(xmax - xmin + 2 * padding, 2 * padding)
    view_height = max(ymax - ymin + 2 * padding, 2 * padding)

    edge_path_parts: list[str] = []
    for source, target in edges:
        edge_path_parts.append(
            "M"
            + compact_number(xs[source])
            + " "
            + compact_number(ys[source])
            + "L"
            + compact_number(xs[target])
            + " "
            + compact_number(ys[target])
        )

    circles = "".join(
        f'<circle class="node" cx="{compact_number(x)}" cy="{compact_number(y)}" r="{compact_number(node_radius(n), 4)}"/>'
        for x, y in zip(xs, ys)
    )

    edge_element = ""
    if edge_path_parts:
        edge_element = f'<path class="edge" d="{"".join(edge_path_parts)}"/>'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{compact_number(view_x)} {compact_number(view_y)} '
        f'{compact_number(view_width)} {compact_number(view_height)}" '
        'preserveAspectRatio="xMidYMid meet" role="img">\n'
        "<style>"
        ".edge{fill:none;stroke:var(--udg-edge,#60a5fa);"
        f"stroke-width:var(--udg-edge-width,{edge_width_px(n):.2f}px);"
        "stroke-opacity:var(--udg-edge-opacity,.38);stroke-linecap:round;"
        "vector-effect:non-scaling-stroke}"
        ".node{fill:var(--udg-node,#f8fafc);stroke:var(--udg-node-outline,#020617);"
        "stroke-width:var(--udg-node-outline-width,.75px);vector-effect:non-scaling-stroke}"
        "</style>\n"
        f'<g class="edges">{edge_element}</g>\n'
        f'<g class="nodes">{circles}</g>\n'
        "</svg>\n"
    )


def summary_from_metadata(metadata: dict[str, object]) -> dict[str, object]:
    n = int(metadata["n"])
    filename = f"{n:0{PAD_WIDTH}d}"
    host = metadata.get("host", {})
    search = metadata.get("search", {})
    return {
        "n": n,
        "edges": int(metadata["edges"]),
        "averageDegree": float(metadata["averageDegree"]),
        "legend": str(metadata["legend"]),
        "host": str(host.get("label", "unknown")) if isinstance(host, dict) else "unknown",
        "svg": f"data/graphs/{filename}.svg",
        "metadata": f"data/records/{filename}.json",
        "updatedAt": str(metadata.get("recordedAt", "")),
        "generatorVersion": str(search.get("generatorVersion", "")) if isinstance(search, dict) else "",
    }


def make_metadata(
    *,
    n: int,
    edges: Sequence[tuple[int, int]],
    points: Sequence[Vec],
    coords: Sequence[complex],
    host: Host,
    run: PeelRun,
    restarts_in_batch: int,
    previous_edges: int | None,
    recorded_at: str,
) -> dict[str, object]:
    edge_count = len(edges)
    filename = f"{n:0{PAD_WIDTH}d}"
    vertex_columns = ["id"] + [f"c{i}" for i in range(host.basis_dimension)] + ["x", "y"]
    vertex_rows: list[list[object]] = []

    for vertex_id, (point, coordinate) in enumerate(zip(points, coords)):
        vertex_rows.append(
            [vertex_id, *point, round(coordinate.real, 12), round(coordinate.imag, 12)]
        )

    metadata: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "id": filename,
        "n": n,
        "edges": edge_count,
        "averageDegree": 2 * edge_count / n,
        "legend": f"{n:,} points · {edge_count:,} unit distances · {host.spec.label}",
        "recordedAt": recorded_at,
        "previousRecordEdges": previous_edges,
        "improvement": None if previous_edges is None else edge_count - previous_edges,
        "files": {
            "svg": f"../graphs/{filename}.svg",
            "metadata": f"{filename}.json",
        },
        "host": host.spec.metadata(host.basis_dimension),
        "search": {
            "algorithm": "randomized low-degree nested peeling",
            "generatorVersion": GENERATOR_VERSION,
            "restart": run.restart,
            "restartsInBatch": restarts_in_batch,
            "runSeed": run.run_seed,
            "choicePool": run.choice_pool,
            "degreeSlack": run.degree_slack,
        },
        "geometry": {
            "embedding": f"ζ_{host.spec.m} ↦ exp(2πi/{host.spec.m})",
            "unitDistanceRule": "an edge difference is a root of unity",
            "vertexTable": {
                "columns": vertex_columns,
                "rows": vertex_rows,
            },
            "edgeTable": {
                "columns": ["source", "target"],
                "rows": [list(edge) for edge in edges],
            },
        },
    }
    return metadata


def emit_winning_records(
    *,
    root: Path,
    winning: dict[int, PeelRun],
    best_edges: Sequence[int],
    old_edges: dict[int, int],
    restarts: int,
    pretty_json: bool,
) -> dict[int, dict[str, object]]:
    """Materialize all newly won records and return their catalog summaries."""
    data_dir = root / "data"
    graphs_dir = data_dir / "graphs"
    records_dir = data_dir / "records"
    graphs_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    runs_by_spec: dict[HostSpec, list[PeelRun]] = {}
    targets_by_run: dict[PeelRun, list[int]] = {}

    for n, run in winning.items():
        targets_by_run.setdefault(run, []).append(n)
        if run not in runs_by_spec.setdefault(run.spec, []):
            runs_by_spec[run.spec].append(run)

    summaries: dict[int, dict[str, object]] = {}
    recorded_at = utc_now()

    for spec, runs in runs_by_spec.items():
        print(f"materializing {spec.label} …", flush=True)
        host = build_host(spec)
        all_coordinates = complex_coordinates(host.points, spec.m)
        host_size = len(host.points)

        for run in runs:
            targets = sorted(targets_by_run[run])
            target_set = set(targets)
            maximum_target = targets[-1]
            addition_order = [run.last_vertex, *reversed(run.removal_order)]
            if len(addition_order) != host_size:
                raise AssertionError("peeling order does not cover the host")

            host_to_local = [-1] * host_size
            local_host_indices: list[int] = []
            local_edges: list[tuple[int, int]] = []

            for size in range(1, maximum_target + 1):
                host_vertex = addition_order[size - 1]
                local_vertex = len(local_host_indices)
                host_to_local[host_vertex] = local_vertex
                local_host_indices.append(host_vertex)

                for neighbor in host.neighbors[host_vertex]:
                    local_neighbor = host_to_local[neighbor]
                    if local_neighbor >= 0:
                        local_edges.append((local_neighbor, local_vertex))

                if size not in target_set:
                    continue

                expected = best_edges[size]
                if len(local_edges) != expected:
                    raise AssertionError(
                        f"edge recount failed for n={size}: {len(local_edges)} != {expected}"
                    )

                selected_points = [host.points[index] for index in local_host_indices]
                selected_coords = [all_coordinates[index] for index in local_host_indices]
                metadata = make_metadata(
                    n=size,
                    edges=local_edges,
                    points=selected_points,
                    coords=selected_coords,
                    host=host,
                    run=run,
                    restarts_in_batch=restarts,
                    previous_edges=old_edges.get(size),
                    recorded_at=recorded_at,
                )
                filename = f"{size:0{PAD_WIDTH}d}"
                svg_text = render_svg(selected_coords, local_edges)
                atomic_write_text(graphs_dir / f"{filename}.svg", svg_text)
                atomic_write_json(records_dir / f"{filename}.json", metadata, pretty=pretty_json)
                summaries[size] = summary_from_metadata(metadata)
                previous = old_edges.get(size)
                improvement = "new" if previous is None else f"+{expected - previous}"
                print(f"  {filename}: {expected:,} edges ({improvement})", flush=True)

    return summaries


def write_catalog(root: Path, records: dict[int, StoredRecord]) -> Path:
    summaries = [records[n].summary for n in sorted(records)]
    catalog = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "maxN": max(records, default=0),
        "recordCount": len(records),
        "records": summaries,
    }
    catalog_path = root / "data" / "catalog.local.json"
    atomic_write_json(catalog_path, catalog, pretty=True)
    return catalog_path


def parse_host_names(value: str) -> set[str]:
    value = value.strip().lower()
    if value == "all":
        return {"hex", "z12", "z18", "z24", "z30", "binary"}
    result = {item.strip() for item in value.split(",") if item.strip()}
    if not result:
        raise argparse.ArgumentTypeError("host list may not be empty")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-n", type=int, required=True, help=f"generate records through n (1..{MAX_N})")
    parser.add_argument("--min-n", type=int, default=1, help="smallest n eligible for improvement")
    parser.add_argument("--restarts", type=int, default=8, help="randomized peeling runs per host")
    parser.add_argument("--seed", type=int, default=0, help="master random seed")
    parser.add_argument(
        "--hosts",
        type=parse_host_names,
        default=parse_host_names("hex,z12,z18,z24,binary"),
        help="comma-separated: hex,z12,z18,z24,z30,binary; or all",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="atlas project directory (default: directory containing this script)",
    )
    parser.add_argument("--pretty-json", action="store_true", help="indent per-record JSON (uses more disk space)")
    args = parser.parse_args()

    if not 1 <= args.max_n <= MAX_N:
        parser.error(f"--max-n must be between 1 and {MAX_N}")
    if not 1 <= args.min_n <= args.max_n:
        parser.error("--min-n must lie between 1 and --max-n")
    if args.restarts < 1:
        parser.error("--restarts must be positive")

    root = args.output.resolve()
    data_dir = root / "data"
    existing = load_existing_records(data_dir)
    old_edges = {n: record.edges for n, record in existing.items()}
    best_edges = [-1] * (args.max_n + 1)
    winning: dict[int, PeelRun] = {}

    for n in range(args.min_n, args.max_n + 1):
        if n in existing:
            best_edges[n] = existing[n].edges

    specs = host_specs(args.max_n, args.hosts)
    print(
        f"searching n={args.min_n}..{args.max_n} with {args.restarts} restart(s) "
        f"across {len(specs)} host(s)",
        flush=True,
    )

    for host_index, spec in enumerate(specs):
        print(f"\n{spec.label}: building {spec.size:,}-vertex host …", flush=True)
        host = build_host(spec)
        degree_sum = sum(len(row) for row in host.neighbors)
        print(
            f"{spec.label}: {len(host.points):,} vertices, {degree_sum // 2:,} host edges",
            flush=True,
        )

        for restart in range(args.restarts):
            run_seed = args.seed + 1_000_003 * host_index + 97_409 * restart
            rng = random.Random(run_seed)
            if restart == 0:
                choice_pool, degree_slack = 1, 0
            else:
                choice_pool = rng.choice((4, 6, 8, 12, 16, 24, 32))
                degree_slack = rng.choice((0, 1, 1, 1, 2))

            removal_order, last_vertex, counts = randomized_peel(
                host,
                args.max_n,
                rng,
                choice_pool=choice_pool,
                degree_slack=degree_slack,
            )
            run = PeelRun(
                spec=spec,
                removal_order=removal_order,
                last_vertex=last_vertex,
                restart=restart + 1,
                run_seed=run_seed,
                choice_pool=choice_pool,
                degree_slack=degree_slack,
            )

            improvements = 0
            for n in range(args.min_n, args.max_n + 1):
                candidate_edges = counts[n]
                if candidate_edges > best_edges[n]:
                    best_edges[n] = candidate_edges
                    winning[n] = run
                    improvements += 1

            print(
                f"  restart {restart + 1:>3}/{args.restarts}: "
                f"pool={choice_pool:<2} slack={degree_slack} · {improvements:,} provisional record(s)",
                flush=True,
            )

        # The winning runs retain only integer removal orders; release the large
        # adjacency structure before building the next host.
        del host

    if winning:
        print(f"\n{len(winning):,} strict record improvement(s) found", flush=True)
        new_summaries = emit_winning_records(
            root=root,
            winning=winning,
            best_edges=best_edges,
            old_edges=old_edges,
            restarts=args.restarts,
            pretty_json=args.pretty_json,
        )
        for n, summary in new_summaries.items():
            existing[n] = StoredRecord(n=n, edges=int(summary["edges"]), summary=summary)
    else:
        print("\nno existing record was beaten", flush=True)

    catalog_path = write_catalog(root, existing)
    print(f"\ncatalog: {catalog_path}", flush=True)


if __name__ == "__main__":
    main()
