#!/usr/bin/env python3
"""Search strict unit-distance records and write a JSON-only animated atlas.

The search runs inside exact algebraic host graphs. Cyclotomic hosts use
integer coefficient vectors in Q(zeta_m), while the Moser host uses exact
four-dimensional integer coordinates. Floating point is used only for the
2-D display coordinates saved to JSON.

Every randomized peeling run is a nested lineage. The generator retains a
small bank of tied strict-record candidates at each n, then chooses a global
sequence that lexicographically:

1. uses the best edge count known to this atlas at every n;
2. maximizes one-vertex growth transitions;
3. maximizes the unit edges introduced by those divisions;
4. minimizes changes of host family;
5. prefers visually balanced candidates among the remaining ties.

No SVG files are produced. The browser renders and animates records directly
from ``data/records/NNNN.json``.
"""

from __future__ import annotations

import argparse
import cmath
import hashlib
import heapq
import itertools
import json
import math
import os
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    import sympy as sp
except ImportError as exc:  # pragma: no cover
    raise SystemExit("SymPy is required: python -m pip install sympy") from exc

from known_optima import KNOWN_EXACT_EDGE_COUNTS, KNOWN_EXACT_MOSER_POINTS, MOSER_STEPS

Vec = tuple[int, ...]
X = sp.Symbol("x")
MAX_N = 2_000
PAD_WIDTH = 4
SCHEMA_VERSION = 2
GENERATOR_VERSION = "4.3"
MAX_CELL_DEATHS = 2
MAX_CELL_DIVISIONS = MAX_CELL_DEATHS + 1
DEFAULT_HOSTS = "hex,moser,z12,z18,z24,z30,z36,binary"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: object, *, pretty: bool = False) -> None:
    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ) + "\n"
    atomic_write_text(path, text)


def add(a: Vec, b: Vec) -> Vec:
    return tuple(x + y for x, y in zip(a, b))


def sub(a: Vec, b: Vec) -> Vec:
    return tuple(x - y for x, y in zip(a, b))


def neg(a: Vec) -> Vec:
    return tuple(-x for x in a)


def product(values: Sequence[int]) -> int:
    result = 1
    for value in values:
        result *= value
    return result


def balanced_lengths(dimension: int, target: int, minimum: int = 2) -> tuple[int, ...]:
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
    basis: str
    m: int = 0
    lengths: tuple[int, ...] = ()
    active_coordinates: tuple[int, ...] = ()
    explicit_points: tuple[Vec, ...] = ()

    @property
    def size(self) -> int:
        if self.kind == "box":
            return product(self.lengths)
        if self.kind == "binary":
            return 1 << len(self.active_coordinates)
        if self.kind == "explicit":
            return len(self.explicit_points)
        raise ValueError(f"unknown host kind {self.kind!r}")

    @property
    def family_key(self) -> str:
        return "moser" if self.basis == "moser" else f"cyclotomic-{self.m}"

    def to_json(self) -> dict[str, object]:
        result: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "basis": self.basis,
            "m": self.m,
            "lengths": list(self.lengths),
            "activeCoordinates": list(self.active_coordinates),
        }
        if self.explicit_points:
            result["explicitPoints"] = [list(point) for point in self.explicit_points]
        return result

    @classmethod
    def from_json(cls, value: dict[str, object]) -> "HostSpec":
        return cls(
            key=str(value["key"]),
            label=str(value["label"]),
            kind=str(value["kind"]),
            basis=str(value["basis"]),
            m=int(value.get("m", 0)),
            lengths=tuple(int(x) for x in value.get("lengths", [])),
            active_coordinates=tuple(int(x) for x in value.get("activeCoordinates", [])),
            explicit_points=tuple(
                tuple(int(x) for x in row) for row in value.get("explicitPoints", [])
            ),
        )

    def public_metadata(self, dimension: int) -> dict[str, object]:
        result: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "family": self.family_key,
            "basisType": self.basis,
            "basisDimension": dimension,
            "hostVertices": self.size,
        }
        if self.basis == "cyclotomic":
            result["cyclotomicOrder"] = self.m
            result["embedding"] = f"zeta_{self.m} -> exp(2*pi*i/{self.m})"
            result["unitDistanceRule"] = "an edge difference is an m-th root of unity"
        else:
            result["coordinateRing"] = "Moser lattice"
            result["basis"] = ["1", "omega_1", "omega_3", "omega_1*omega_3"]
            result["embedding"] = "omega_1=exp(i*pi/3), omega_3=(5+i*sqrt(11))/6"
            result["unitDistanceRule"] = "an edge difference is one of the 18 Moser unit vectors"
        if self.lengths:
            result["lengths"] = list(self.lengths)
        if self.active_coordinates:
            result["activeCoordinates"] = list(self.active_coordinates)
        if self.explicit_points:
            result["constructionVertices"] = len(self.explicit_points)
        return result


@dataclass
class Host:
    spec: HostSpec
    points: list[Vec]
    steps: list[Vec]
    neighbors: list[list[int]]
    coordinates: list[complex]
    dimension: int


@dataclass
class RunArchive:
    id: str
    spec: HostSpec
    addition_order: list[int]
    edge_counts: list[int]
    aesthetics: list[float]
    fingerprints: list[str]
    search: dict[str, object]
    exact: bool = False

    @property
    def max_n(self) -> int:
        return len(self.edge_counts) - 1

    def to_json(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatorVersion": GENERATOR_VERSION,
            "id": self.id,
            "spec": self.spec.to_json(),
            "additionOrder": self.addition_order,
            "edgeCounts": self.edge_counts,
            "aesthetics": self.aesthetics,
            "fingerprints": self.fingerprints,
            "search": self.search,
            "exact": self.exact,
        }

    @classmethod
    def from_json(cls, value: dict[str, object]) -> "RunArchive":
        return cls(
            id=str(value["id"]),
            spec=HostSpec.from_json(dict(value["spec"])),
            addition_order=[int(x) for x in value["additionOrder"]],
            edge_counts=[int(x) for x in value["edgeCounts"]],
            aesthetics=[float(x) for x in value["aesthetics"]],
            fingerprints=[str(x) for x in value["fingerprints"]],
            search=dict(value.get("search", {})),
            exact=bool(value.get("exact", False)),
        )


@dataclass(frozen=True)
class Candidate:
    n: int
    edges: int
    run_id: str
    spec_key: str
    family_key: str
    fingerprint: str
    aesthetic: float
    streak: int
    exact: bool


@dataclass
class GraphData:
    candidate: Candidate
    spec: HostSpec
    dimension: int
    integer_points: list[Vec]
    raw_coordinates: list[complex]
    coordinates: list[complex]
    edges: list[tuple[int, int]]
    degrees: list[int]


@dataclass(frozen=True)
class Isometry:
    angle: float
    reflect: bool
    translation: complex

    def linear(self, value: complex) -> complex:
        if self.reflect:
            value = value.conjugate()
        return value * cmath.exp(1j * self.angle)

    def apply(self, value: complex) -> complex:
        return self.linear(value) + self.translation


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
            raise ValueError(f"{spec.key}: expected {dimension} box dimensions")
        offsets = [-(length // 2) for length in spec.lengths]
        ranges = [range(offset, offset + length) for offset, length in zip(offsets, spec.lengths)]
        return list(itertools.product(*ranges))
    if spec.kind == "binary":
        points: list[Vec] = []
        for bits in itertools.product((0, 1), repeat=len(spec.active_coordinates)):
            vector = [0] * dimension
            for coordinate, bit in zip(spec.active_coordinates, bits):
                vector[coordinate] = bit
            points.append(tuple(vector))
        return points
    if spec.kind == "explicit":
        return list(spec.explicit_points)
    raise ValueError(f"unknown host kind {spec.kind!r}")


def cyclotomic_coordinates(points: Sequence[Vec], m: int) -> list[complex]:
    zeta = cmath.exp(2j * math.pi / m)
    powers = [zeta**i for i in range(len(points[0]))]
    return [sum(coefficient * power for coefficient, power in zip(point, powers)) for point in points]


def moser_coordinates(points: Sequence[Vec]) -> list[complex]:
    omega_1 = complex(0.5, math.sqrt(3) / 2)
    omega_3 = complex(5 / 6, math.sqrt(11) / 6)
    basis = (1 + 0j, omega_1, omega_3, omega_1 * omega_3)
    return [sum(coefficient * value for coefficient, value in zip(point, basis)) for point in points]


def vector_to_complex(vector: Vec, spec: HostSpec) -> complex:
    return moser_coordinates([vector])[0] if spec.basis == "moser" else cyclotomic_coordinates([vector], spec.m)[0]


def build_host(spec: HostSpec) -> Host:
    if spec.basis == "moser":
        dimension, steps = 4, list(MOSER_STEPS)
    elif spec.basis == "cyclotomic":
        dimension, steps = cyclotomic_steps(spec.m)
    else:
        raise ValueError(f"unknown basis {spec.basis!r}")

    points = points_for_spec(spec, dimension)
    if len(set(points)) != len(points):
        raise ValueError(f"{spec.key}: duplicate host point")
    index = {point: i for i, point in enumerate(points)}
    neighbors: list[list[int]] = [[] for _ in points]
    for i, point in enumerate(points):
        for step in steps:
            j = index.get(add(point, step))
            if j is not None:
                neighbors[i].append(j)

    coordinates = moser_coordinates(points) if spec.basis == "moser" else cyclotomic_coordinates(points, spec.m)
    return Host(spec, points, steps, neighbors, coordinates, dimension)


def spread_coordinates(dimension: int, count: int) -> tuple[int, ...]:
    left = (count + 1) // 2
    right = count - left
    result = list(range(left))
    if right:
        result.extend(range(dimension - right, dimension))
    return tuple(result)


def host_specs(max_n: int, requested: set[str]) -> list[HostSpec]:
    available = {"hex", "moser", "z12", "z18", "z24", "z30", "z36", "binary"}
    unknown = requested - available
    if unknown:
        raise ValueError(f"unknown host name(s): {', '.join(sorted(unknown))}")

    specs: list[HostSpec] = []

    def add_box(name: str, m: int, factor: float, minimum: int = 2) -> None:
        dimension = int(sp.totient(m))
        lengths = balanced_lengths(dimension, math.ceil(max_n * factor), minimum)
        specs.append(
            HostSpec(
                key=f"{name}-{'x'.join(map(str, lengths))}",
                label=f"zeta-{m} box {' x '.join(map(str, lengths))}",
                kind="box",
                basis="cyclotomic",
                m=m,
                lengths=lengths,
            )
        )

    if "hex" in requested:
        add_box("hex", 6, 1.10, minimum=1)
    if "moser" in requested:
        lengths = balanced_lengths(4, math.ceil(max_n * 1.35), minimum=2)
        specs.append(
            HostSpec(
                key=f"moser-{'x'.join(map(str, lengths))}",
                label=f"Moser box {' x '.join(map(str, lengths))}",
                kind="box",
                basis="moser",
                lengths=lengths,
            )
        )
    for name, m, factor in (
        ("z12", 12, 1.18),
        ("z18", 18, 1.22),
        ("z24", 24, 1.25),
        ("z30", 30, 1.25),
        ("z36", 36, 1.28),
    ):
        if name in requested:
            add_box(name, m, factor)
    if "binary" in requested:
        target = max(2, math.ceil(max_n * 1.35))
        for m in (36, 60):
            dimension = int(sp.totient(m))
            count = min(dimension, max(1, math.ceil(math.log2(target))))
            active = spread_coordinates(dimension, count)
            specs.append(
                HostSpec(
                    key=f"z{m}-binary-{count}",
                    label=f"zeta-{m} binary {count}-cube",
                    kind="binary",
                    basis="cyclotomic",
                    m=m,
                    active_coordinates=active,
                )
            )

    if not specs:
        raise ValueError("at least one host must be selected")
    if any(spec.size < max_n for spec in specs):
        raise AssertionError("host construction undershot max_n")
    return specs


def normalized_host_radii(coordinates: Sequence[complex]) -> list[float]:
    center = sum(coordinates, 0j) / len(coordinates)
    radii = [abs(point - center) for point in coordinates]
    mean = sum(radii) / max(1, len(radii))
    variance = sum((radius - mean) ** 2 for radius in radii) / max(1, len(radii))
    std = math.sqrt(variance) or 1.0
    return [(radius - mean) / std for radius in radii]


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
    compactness_bias: float,
) -> tuple[list[int], list[int]]:
    size = len(host.points)
    alive = bytearray(b"\x01") * size
    degrees = [len(row) for row in host.neighbors]
    radii = normalized_host_radii(host.coordinates)
    edges = sum(degrees) // 2
    alive_count = size
    counts = [-1] * (max_n + 1)
    if alive_count <= max_n:
        counts[alive_count] = edges

    def tie_value(vertex: int) -> float:
        return -compactness_bias * radii[vertex] + rng.random()

    heap = [(degree, tie_value(vertex), vertex) for vertex, degree in enumerate(degrees)]
    heapq.heapify(heap)
    removal_order: list[int] = []

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

        weights = []
        for degree, _, vertex in candidates:
            degree_penalty = 1.35 * (degree - minimum_degree)
            outer_reward = compactness_bias * max(-2.5, min(2.5, radii[vertex]))
            weights.append(math.exp(-degree_penalty + outer_reward))
        chosen_index = rng.choices(range(len(candidates)), weights=weights, k=1)[0]
        _, _, vertex = candidates[chosen_index]
        for i, item in enumerate(candidates):
            if i != chosen_index:
                heapq.heappush(heap, item)

        alive[vertex] = 0
        removal_order.append(vertex)
        edges -= degrees[vertex]
        alive_count -= 1
        for neighbor in host.neighbors[vertex]:
            if alive[neighbor]:
                degrees[neighbor] -= 1
                heapq.heappush(heap, (degrees[neighbor], tie_value(neighbor), neighbor))
        if alive_count <= max_n:
            counts[alive_count] = edges

    survivor = alive.index(1)
    return [survivor, *reversed(removal_order)], counts


def splitmix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9 & 0xFFFFFFFFFFFFFFFF
    value = (value ^ (value >> 27)) * 0x94D049BB133111EB & 0xFFFFFFFFFFFFFFFF
    return value ^ (value >> 31)


def prefix_metrics(host: Host, addition_order: Sequence[int], max_n: int) -> tuple[list[float], list[str]]:
    limit = min(max_n, len(addition_order))
    aesthetics = [0.0] * (max_n + 1)
    fingerprints = [""] * (max_n + 1)
    local_index = [-1] * len(host.points)
    degrees = [0] * len(host.points)
    sum_degree_squares = 0.0
    edge_count = 0
    sx = sy = sxx = syy = sxy = 0.0
    xmin = ymin = math.inf
    xmax = ymax = -math.inf
    fingerprint = splitmix64(int.from_bytes(hashlib.sha256(host.spec.key.encode()).digest()[:8], "little"))

    for n, host_vertex in enumerate(addition_order[:limit], start=1):
        local_index[host_vertex] = n - 1
        k = 0
        for neighbor in host.neighbors[host_vertex]:
            if local_index[neighbor] >= 0:
                old = degrees[neighbor]
                sum_degree_squares += 2 * old + 1
                degrees[neighbor] = old + 1
                k += 1
        degrees[host_vertex] = k
        sum_degree_squares += k * k
        edge_count += k

        point = host.coordinates[host_vertex]
        x, y = point.real, point.imag
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        xmin, xmax = min(xmin, x), max(xmax, x)
        ymin, ymax = min(ymin, y), max(ymax, y)

        cx, cy = sx / n, sy / n
        xx = max(0.0, sxx / n - cx * cx)
        yy = max(0.0, syy / n - cy * cy)
        xy = sxy / n - cx * cy
        trace = xx + yy
        anisotropy = math.sqrt(max(0.0, (xx - yy) ** 2 + 4 * xy * xy))
        isotropy = 1.0 if trace < 1e-12 else max(0.0, 1.0 - anisotropy / trace)
        span = max(xmax - xmin, ymax - ymin, 1e-9)
        box_center = complex((xmin + xmax) / 2, (ymin + ymax) / 2)
        centering = math.exp(-4.0 * abs(complex(cx, cy) - box_center) / span)
        mean_degree = 2 * edge_count / n
        degree_variance = max(0.0, sum_degree_squares / n - mean_degree * mean_degree)
        regularity = 1.0 / (1.0 + degree_variance / 4.0)
        aesthetics[n] = round(0.62 * isotropy + 0.23 * centering + 0.15 * regularity, 8)
        fingerprint ^= splitmix64(host_vertex + 1)
        fingerprints[n] = f"{fingerprint:016x}"

    return aesthetics, fingerprints


def run_identifier(spec: HostSpec, addition_order: Sequence[int], search: dict[str, object]) -> str:
    payload = json.dumps(
        {"spec": spec.to_json(), "additionOrder": list(addition_order), "search": search},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def make_search_run(
    host: Host,
    *,
    max_n: int,
    restart: int,
    run_seed: int,
    choice_pool: int,
    degree_slack: int,
    compactness_bias: float,
) -> RunArchive:
    rng = random.Random(run_seed)
    addition_order, counts = randomized_peel(
        host,
        max_n,
        rng,
        choice_pool=choice_pool,
        degree_slack=degree_slack,
        compactness_bias=compactness_bias,
    )
    aesthetics, fingerprints = prefix_metrics(host, addition_order, max_n)
    search: dict[str, object] = {
        "algorithm": "randomized low-degree nested peeling",
        "restart": restart,
        "runSeed": run_seed,
        "choicePool": choice_pool,
        "degreeSlack": degree_slack,
        "compactnessBias": compactness_bias,
    }
    run_id = run_identifier(host.spec, addition_order, search)
    return RunArchive(run_id, host.spec, addition_order, counts, aesthetics, fingerprints, search)


def make_exact_runs(max_n: int) -> list[RunArchive]:
    runs: list[RunArchive] = []
    for n, points in KNOWN_EXACT_MOSER_POINTS.items():
        if n > max_n:
            continue
        spec = HostSpec(
            key=f"known-exact-moser-{n}",
            label=f"known optimal Moser graph n={n}",
            kind="explicit",
            basis="moser",
            explicit_points=points,
        )
        host = build_host(spec)
        edge_count = sum(len(row) for row in host.neighbors) // 2
        expected = KNOWN_EXACT_EDGE_COUNTS[n]
        if edge_count != expected:
            raise AssertionError(f"exact n={n}: {edge_count} edges, expected {expected}")
        addition_order = list(range(n))
        aesthetics, fingerprints = prefix_metrics(host, addition_order, n)
        counts = [-1] * (n + 1)
        counts[n] = edge_count
        search: dict[str, object] = {
            "algorithm": "published exact Moser construction",
            "source": {
                "title": "The Erdos unit distance problem for small point sets",
                "authors": "Boris Alexeev, Dustin G. Mixon, Hans Parshall",
                "arxiv": "2412.11914v2",
                "sequence": "OEIS A186705",
            },
        }
        run_id = run_identifier(spec, addition_order, search)
        runs.append(RunArchive(run_id, spec, addition_order, counts, aesthetics, fingerprints, search, True))
    return runs


def run_path(root: Path, run_id: str) -> Path:
    return root / "data" / "search" / "runs" / f"{run_id}.json"


def load_runs(root: Path) -> dict[str, RunArchive]:
    directory = root / "data" / "search" / "runs"
    runs: dict[str, RunArchive] = {}
    if not directory.exists():
        return runs
    for path in sorted(directory.glob("*.json")):
        try:
            run = RunArchive.from_json(json.loads(path.read_text(encoding="utf-8")))
            runs[run.id] = run
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"warning: skipped bad search run {path}: {exc}", file=sys.stderr)
    return runs


def save_runs(root: Path, runs: Iterable[RunArchive], *, pretty: bool) -> None:
    for run in runs:
        path = run_path(root, run.id)
        if not path.exists():
            atomic_write_json(path, run.to_json(), pretty=pretty)


def load_existing_edge_counts(root: Path, max_n: int) -> list[int]:
    result = [-1] * (max_n + 1)
    for name in ("catalog.local.json", "catalog.json"):
        path = root / "data" / name
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if int(value.get("schemaVersion", -1)) != SCHEMA_VERSION:
                raise ValueError("older atlas schema; use a clean output directory or --reset-search-state")
            for summary in value.get("records", []):
                n = int(summary["n"])
                if 1 <= n <= max_n:
                    result[n] = int(summary["edges"])
            return result
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SystemExit(f"could not read {path}: {exc}") from exc
    return result


def compute_best_edges(runs: Sequence[RunArchive], existing: Sequence[int], max_n: int) -> list[int]:
    best = list(existing)
    for run in runs:
        for n in range(1, min(max_n, run.max_n) + 1):
            best[n] = max(best[n], run.edge_counts[n])
    missing = [n for n in range(1, max_n + 1) if best[n] < 0]
    if missing:
        raise RuntimeError(f"no candidate for n={missing[0]}")
    return best


def streak_lengths(matches: Sequence[bool]) -> list[int]:
    left = [0] * len(matches)
    right = [0] * len(matches)
    for i in range(1, len(matches)):
        left[i] = left[i - 1] + 1 if matches[i] else 0
    for i in range(len(matches) - 2, 0, -1):
        right[i] = right[i + 1] + 1 if matches[i] else 0
    return [left[i] + right[i] - 1 if matches[i] else 0 for i in range(len(matches))]


def build_candidate_layers(
    runs: Sequence[RunArchive],
    best_edges: Sequence[int],
    max_n: int,
    ties_per_n: int,
    previous_sequence: dict[int, str],
) -> list[list[Candidate]]:
    layers: list[list[Candidate]] = [[] for _ in range(max_n + 1)]
    for run in runs:
        limit = min(max_n, run.max_n)
        matches = [False] * (max_n + 1)
        for n in range(1, limit + 1):
            matches[n] = run.edge_counts[n] == best_edges[n]
        streaks = streak_lengths(matches)
        for n in range(1, limit + 1):
            if matches[n]:
                layers[n].append(
                    Candidate(
                        n=n,
                        edges=best_edges[n],
                        run_id=run.id,
                        spec_key=run.spec.key,
                        family_key=run.spec.family_key,
                        fingerprint=run.fingerprints[n],
                        aesthetic=run.aesthetics[n],
                        streak=streaks[n],
                        exact=run.exact,
                    )
                )

    for n in range(1, max_n + 1):
        deduplicated: dict[tuple[str, str], Candidate] = {}
        for candidate in layers[n]:
            key = (candidate.spec_key, candidate.fingerprint)
            old = deduplicated.get(key)
            if old is None or (candidate.streak, candidate.aesthetic) > (old.streak, old.aesthetic):
                deduplicated[key] = candidate
        pool = list(deduplicated.values())
        if not pool:
            raise RuntimeError(f"record edge count for n={n} has no archived candidate")

        chosen: list[Candidate] = []

        def add_unique(items: Iterable[Candidate]) -> None:
            seen = {item.run_id for item in chosen}
            for item in items:
                if item.run_id not in seen:
                    chosen.append(item)
                    seen.add(item.run_id)
                if len(chosen) >= ties_per_n:
                    return

        half = max(1, ties_per_n // 2)
        add_unique(sorted(pool, key=lambda c: (c.streak, c.aesthetic, c.exact), reverse=True)[:half])
        add_unique(sorted(pool, key=lambda c: (c.aesthetic, c.streak, c.exact), reverse=True))

        old_run = previous_sequence.get(n)
        old = next((item for item in pool if item.run_id == old_run), None)
        if old and old.run_id not in {item.run_id for item in chosen}:
            if len(chosen) >= ties_per_n:
                chosen[-1] = old
            else:
                chosen.append(old)

        exact = next((item for item in pool if item.exact), None)
        if exact and exact.run_id not in {item.run_id for item in chosen}:
            if len(chosen) >= ties_per_n:
                chosen[-1] = exact
            else:
                chosen.append(exact)
        layers[n] = chosen[:ties_per_n]
    return layers


def load_previous_sequence(root: Path) -> dict[int, str]:
    path = root / "data" / "sequence.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {int(row["n"]): str(row["runId"]) for row in value.get("selection", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def cached_host(spec: HostSpec, host_cache: dict[str, Host]) -> Host:
    host = host_cache.get(spec.key)
    if host is None:
        host = build_host(spec)
        host_cache[spec.key] = host
    return host


def candidate_prefix_points(candidate: Candidate, runs: dict[str, RunArchive], host_cache: dict[str, Host]) -> list[Vec]:
    run = runs[candidate.run_id]
    host = cached_host(run.spec, host_cache)
    return [host.points[index] for index in run.addition_order[:candidate.n]]


def translation_subset_mapping(smaller: Sequence[Vec], larger: Sequence[Vec]) -> tuple[Vec, list[int], int] | None:
    """Find ``smaller + translation`` inside ``larger`` in near-linear time.

    Since ``larger`` has exactly one extra point, trying that omitted point fixes
    the translation from the coordinate sums.  In practice only very few
    omitted points yield an integral translation, after which a hash lookup
    verifies the subset.
    """
    if len(larger) != len(smaller) + 1 or not smaller:
        return None
    dimension = len(smaller[0])
    count = len(smaller)
    smaller_sum = [sum(point[axis] for point in smaller) for axis in range(dimension)]
    larger_sum = [sum(point[axis] for point in larger) for axis in range(dimension)]
    lookup = {point: index for index, point in enumerate(larger)}

    for added, omitted_point in enumerate(larger):
        numerators = [larger_sum[axis] - omitted_point[axis] - smaller_sum[axis] for axis in range(dimension)]
        if any(value % count for value in numerators):
            continue
        translation = tuple(value // count for value in numerators)
        mapping: list[int] = []
        for point in smaller:
            index = lookup.get(add(point, translation))
            if index is None or index == added:
                break
            mapping.append(index)
        else:
            if len(set(mapping)) == count:
                return translation, mapping, added
    return None


def simple_transition(
    left: Candidate,
    right: Candidate,
    runs: dict[str, RunArchive],
    host_cache: dict[str, Host],
    cache: dict[tuple[str, int, str, int], bool],
) -> bool:
    if right.n != left.n + 1:
        return False
    if left.run_id == right.run_id:
        return True
    key = (left.run_id, left.n, right.run_id, right.n)
    if key in cache:
        return cache[key]
    left_run, right_run = runs[left.run_id], runs[right.run_id]
    # Cross-run subset checks are useful for the small exact regime but make the
    # layered DP quadratic in n at large sizes.  Above this point, continuity is
    # credited only when candidates share an archived nested run; materialization
    # still detects any accidental exact subset in near-linear time.
    possible = left.n <= 64 and left_run.spec.basis == right_run.spec.basis and left_run.spec.m == right_run.spec.m
    result = False
    if possible:
        result = translation_subset_mapping(
            candidate_prefix_points(left, runs, host_cache),
            candidate_prefix_points(right, runs, host_cache),
        ) is not None
    cache[key] = result
    return result


def choose_sequence(layers: Sequence[Sequence[Candidate]], runs: dict[str, RunArchive], max_n: int) -> list[Candidate]:
    host_cache: dict[str, Host] = {}
    simple_cache: dict[tuple[str, int, str, int], bool] = {}
    scores: list[tuple[int, int, int, int, int]] = [
        (0, 0, 0, int(candidate.aesthetic * 1_000_000), candidate.streak) for candidate in layers[1]
    ]
    parents: list[list[int]] = [[] for _ in range(max_n + 1)]
    parents[1] = [-1] * len(layers[1])

    for n in range(2, max_n + 1):
        next_scores: list[tuple[int, int, int, int, int]] = []
        parent_row: list[int] = []
        for current in layers[n]:
            best_score: tuple[int, int, int, int, int] | None = None
            best_parent = -1
            for index, previous in enumerate(layers[n - 1]):
                old = scores[index]
                division = simple_transition(previous, current, runs, host_cache, simple_cache)
                score = (
                    old[0] + int(division),
                    # Prefer the most consequential divisions before judging
                    # individual-frame symmetry. In particular, preserving the
                    # three-edge 6 -> 7 hexagon division beats a prettier n=6
                    # frame that must transmute into n=7.
                    old[1] + (current.edges - previous.edges if division else 0),
                    old[2] + int(previous.family_key == current.family_key),
                    old[3] + int(current.aesthetic * 1_000_000),
                    old[4] + current.streak,
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best_parent = index
            assert best_score is not None
            next_scores.append(best_score)
            parent_row.append(best_parent)
        scores = next_scores
        parents[n] = parent_row

    index = max(range(len(scores)), key=lambda i: scores[i])
    sequence = [layers[max_n][index]]
    for n in range(max_n, 1, -1):
        index = parents[n][index]
        sequence.append(layers[n - 1][index])
    sequence.reverse()
    return sequence


def materialize_candidate(candidate: Candidate, runs: dict[str, RunArchive], host_cache: dict[str, Host]) -> GraphData:
    run = runs[candidate.run_id]
    host = cached_host(run.spec, host_cache)
    selected = run.addition_order[:candidate.n]
    host_to_local = {host_vertex: local for local, host_vertex in enumerate(selected)}
    edges: list[tuple[int, int]] = []
    degrees = [0] * candidate.n
    for local, host_vertex in enumerate(selected):
        for neighbor in host.neighbors[host_vertex]:
            other = host_to_local.get(neighbor)
            if other is not None and other < local:
                edges.append((other, local))
                degrees[other] += 1
                degrees[local] += 1
    if len(edges) != candidate.edges:
        raise AssertionError(f"n={candidate.n}: {len(edges)} edges, expected {candidate.edges}")
    points = [host.points[index] for index in selected]
    coords = [host.coordinates[index] for index in selected]
    return GraphData(candidate, run.spec, host.dimension, points, coords, coords[:], edges, degrees)


def centroid(points: Sequence[complex]) -> complex:
    return sum(points, 0j) / max(1, len(points))


def principal_angle(points: Sequence[complex]) -> float:
    if len(points) < 2:
        return 0.0
    center = centroid(points)
    xx = yy = xy = 0.0
    for point in points:
        x = point.real - center.real
        y = point.imag - center.imag
        xx += x * x
        yy += y * y
        xy += x * y
    return 0.5 * math.atan2(2 * xy, xx - yy)


def first_isometry(points: Sequence[complex]) -> Isometry:
    angle = -principal_angle(points)
    rotation = cmath.exp(1j * angle)
    center = centroid(points)
    return Isometry(angle, False, -center * rotation)


def apply_isometry(points: Sequence[complex], transform: Isometry) -> list[complex]:
    return [transform.apply(point) for point in points]


def normalized_points(points: Sequence[complex]) -> list[complex]:
    center = centroid(points)
    centered = [point - center for point in points]
    rms = math.sqrt(sum(abs(point) ** 2 for point in centered) / max(1, len(centered))) or 1.0
    return [point / rms for point in centered]


def hilbert_index(x: int, y: int, bits: int = 12) -> int:
    n = 1 << bits
    d = 0
    s = n >> 1
    while s:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = n - 1 - x
                y = n - 1 - y
            x, y = y, x
        s >>= 1
    return d


def space_filling_order(points: Sequence[complex], combined_bounds: tuple[float, float, float, float]) -> list[int]:
    xmin, xmax, ymin, ymax = combined_bounds
    spanx = xmax - xmin or 1.0
    spany = ymax - ymin or 1.0
    limit = (1 << 12) - 1
    keyed = []
    for index, point in enumerate(points):
        x = max(0, min(limit, round((point.real - xmin) / spanx * limit)))
        y = max(0, min(limit, round((point.imag - ymin) / spany * limit)))
        keyed.append((hilbert_index(x, y), abs(point), index))
    keyed.sort()
    return [index for _, _, index in keyed]


def hilbert_mapping_with_replacements(
    source: Sequence[complex],
    target: Sequence[complex],
    target_degrees: Sequence[int],
    *,
    max_deaths: int = MAX_CELL_DEATHS,
) -> tuple[list[tuple[int, int]], list[int], list[int], float]:
    """Spatially match adjacent frames, allowing a small amount of cell renewal.

    The target has one more point, so dropping ``d`` old cells necessarily
    introduces ``d + 1`` new cells.  A narrow dynamic-programming alignment of
    the Hilbert orders considers zero through two deaths (and therefore one
    through three divisions).  The skip cost prevents needless replacement,
    while allowing obvious long-distance matches to become local deaths and
    births instead.
    """
    if len(target) != len(source) + 1:
        raise ValueError("Hilbert one-extra mapping requires target size source+1")
    all_points = [*source, *target]
    combined_bounds = (
        min(point.real for point in all_points),
        max(point.real for point in all_points),
        min(point.imag for point in all_points),
        max(point.imag for point in all_points),
    )
    source_order = space_filling_order(source, combined_bounds)
    target_order = space_filling_order(target, combined_bounds)
    # A skip costs about the same as moving a cell one fifth of the normalized
    # drawing width. New high-degree cells get a tiny preference as divisions.
    skip_cost = 0.04
    start = (0, 0, 0, 0)
    costs = {start: 0.0}
    parents: dict[tuple[int, int, int, int], tuple[tuple[int, int, int, int], str]] = {}
    layers: list[set[tuple[int, int, int, int]]] = [set() for _ in range(len(source) + len(target) + 1)]
    layers[0].add(start)

    def relax(
        state: tuple[int, int, int, int],
        next_state: tuple[int, int, int, int],
        next_cost: float,
        operation: str,
    ) -> None:
        if next_cost < costs.get(next_state, math.inf):
            costs[next_state] = next_cost
            parents[next_state] = (state, operation)
            layers[next_state[0] + next_state[1]].add(next_state)

    for total in range(len(source) + len(target) + 1):
        for state in layers[total]:
            cost = costs[state]
            i, j, deaths, divisions = state
            if i < len(source) and j < len(target):
                next_state = (i + 1, j + 1, deaths, divisions)
                next_cost = cost + abs(source[source_order[i]] - target[target_order[j]]) ** 2
                relax(state, next_state, next_cost, "match")
            if i < len(source) and deaths < max_deaths:
                next_state = (i + 1, j, deaths + 1, divisions)
                next_cost = cost + skip_cost
                relax(state, next_state, next_cost, "death")
            if j < len(target) and divisions < min(MAX_CELL_DIVISIONS, max_deaths + 1):
                next_state = (i, j + 1, deaths, divisions + 1)
                next_cost = cost + skip_cost + target_degrees[target_order[j]] * 1e-5
                relax(state, next_state, next_cost, "division")

    terminals = [
        state for state in costs
        if state[0] == len(source) and state[1] == len(target) and state[3] == state[2] + 1
    ]
    best = min(terminals, key=lambda state: costs[state])
    pairs: list[tuple[int, int]] = []
    removed: list[int] = []
    added: list[int] = []
    state = best
    while state != start:
        previous, operation = parents[state]
        i, j, _, _ = previous
        if operation == "match":
            pairs.append((source_order[i], target_order[j]))
        elif operation == "death":
            removed.append(source_order[i])
        else:
            added.append(target_order[j])
        state = previous
    pairs.reverse()
    removed.reverse()
    added.reverse()
    return pairs, removed, added, costs[best] / max(1, len(pairs))


def retained_edge_count(
    previous_edges: Sequence[tuple[int, int]],
    current_edges: Sequence[tuple[int, int]],
    pairs: Sequence[tuple[int, int]],
) -> tuple[int, int]:
    mapping = dict(pairs)
    current_set = {tuple(sorted(edge)) for edge in current_edges}
    surviving = [(a, b) for a, b in previous_edges if a in mapping and b in mapping]
    retained = sum(
        tuple(sorted((mapping[a], mapping[b]))) in current_set
        for a, b in surviving
    )
    return retained, len(surviving)


def choose_transmutation_alignment(previous: GraphData, current: GraphData) -> tuple[Isometry, list[tuple[int, int]], list[int], list[int], float, str]:
    previous_normalized = normalized_points(previous.coordinates)
    previous_angle = principal_angle(previous.coordinates)
    raw_principal = principal_angle(current.raw_coordinates)

    best: tuple[int, float, Isometry, list[tuple[int, int]], list[int], list[int], str] | None = None
    for reflect in (False, True):
        reflected_principal = -raw_principal if reflect else raw_principal
        base = previous_angle - reflected_principal
        for step in range(12):
            angle = base + step * math.pi / 6
            unit = cmath.exp(1j * angle)
            linear = [((point.conjugate() if reflect else point) * unit) for point in current.raw_coordinates]
            translation = centroid(previous.coordinates) - centroid(linear)
            displayed = [point + translation for point in linear]
            displayed_normalized = normalized_points(displayed)
            full_pairs, _, full_added, full_cost = hilbert_mapping_with_replacements(
                previous_normalized, displayed_normalized, current.degrees, max_deaths=0
            )
            full_retained, _ = retained_edge_count(previous.edges, current.edges, full_pairs)
            if full_retained == len(previous.edges):
                # Graph containment is the strongest signal: never manufacture
                # deaths when every old edge survives under a one-cell mapping.
                # This also detects equivalent constructions expressed in
                # different algebraic hosts, such as the n=5 -> 6 division.
                item = (
                    0, full_cost, Isometry(angle, reflect, translation),
                    full_pairs, [], full_added, "growth",
                )
                if best is None or item[:2] < best[:2]:
                    best = item
                continue

            pairs, removed, added, cost = hilbert_mapping_with_replacements(
                previous_normalized, displayed_normalized, current.degrees
            )
            retained, surviving = retained_edge_count(previous.edges, current.edges, pairs)
            renewal = bool(removed) and retained == surviving and retained * 2 > len(previous.edges)
            if removed and not renewal:
                # Death is only meaningful when the remaining graph stays
                # visibly intact. Otherwise use an ordinary transmutation with
                # every old cell mapped and no misleading death animation.
                pairs, removed, added, cost = hilbert_mapping_with_replacements(
                    previous_normalized, displayed_normalized, current.degrees, max_deaths=0
                )
            kind = "renewal" if renewal else "transmutation"
            item = (1 if renewal else 2, cost, Isometry(angle, reflect, translation), pairs, removed, added, kind)
            if best is None or item[:2] < best[:2]:
                best = item
    assert best is not None
    return best[2], best[3], best[4], best[5], best[1], best[6]


def exact_simple_alignment(previous: GraphData, current: GraphData, previous_transform: Isometry) -> tuple[Isometry, list[int], int] | None:
    if previous.spec.basis != current.spec.basis or previous.spec.m != current.spec.m:
        return None
    result = translation_subset_mapping(previous.integer_points, current.integer_points)
    if result is None:
        return None
    translation_vector, mapping, added = result
    complex_translation = vector_to_complex(translation_vector, current.spec)
    adjusted = Isometry(
        previous_transform.angle,
        previous_transform.reflect,
        previous_transform.translation - previous_transform.linear(complex_translation),
    )
    return adjusted, mapping, added


def mapped_edges_preserved(previous_edges: Sequence[tuple[int, int]], current_edges: Sequence[tuple[int, int]], mapping: Sequence[int]) -> bool:
    current_set = {tuple(sorted(edge)) for edge in current_edges}
    return all(tuple(sorted((mapping[a], mapping[b]))) in current_set for a, b in previous_edges)


def spawn_point(graph: GraphData, added: int) -> tuple[complex, list[int]]:
    neighbors: list[int] = []
    for a, b in graph.edges:
        if a == added:
            neighbors.append(b)
        elif b == added:
            neighbors.append(a)
    if neighbors:
        return centroid([graph.coordinates[i] for i in neighbors]), sorted(neighbors)
    return centroid(graph.coordinates), []


def bounds(points: Sequence[complex]) -> list[float]:
    xs = [point.real for point in points]
    ys = [point.imag for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def round_number(value: float, digits: int = 10) -> float:
    return 0.0 if abs(value) < 0.5 * 10 ** (-digits) else round(value, digits)


def make_record(
    graph: GraphData,
    run: RunArchive,
    transition: dict[str, object] | None,
    recorded_at: str,
    *,
    include_integer_coordinates: bool,
) -> dict[str, object]:
    n = graph.candidate.n
    geometry: dict[str, object] = {
        "coordinates": [[round_number(point.real), round_number(point.imag)] for point in graph.coordinates],
        "edges": [value for edge in graph.edges for value in edge],
        "bounds": [round_number(value) for value in bounds(graph.coordinates)],
    }
    if include_integer_coordinates:
        geometry["integerCoordinates"] = [list(point) for point in graph.integer_points]

    if n in KNOWN_EXACT_EDGE_COUNTS and graph.candidate.edges == KNOWN_EXACT_EDGE_COUNTS[n]:
        optimality: dict[str, object] = {
            "status": "proven optimal",
            "sequence": "OEIS A186705",
            "certifiedThrough": 21,
        }
    else:
        optimality = {
            "status": "strict atlas record",
            "meaning": "best edge count present in this atlas search state; not a proof of global optimality",
        }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": f"{n:0{PAD_WIDTH}d}",
        "n": n,
        "edges": graph.candidate.edges,
        "averageDegree": 2 * graph.candidate.edges / n,
        "legend": f"{n:,} points · {graph.candidate.edges:,} unit distances · {run.spec.label}",
        "recordedAt": recorded_at,
        "optimality": optimality,
        "host": run.spec.public_metadata(graph.dimension),
        "search": {"generatorVersion": GENERATOR_VERSION, **run.search},
        "lineage": {
            "runId": run.id,
            "fingerprint": graph.candidate.fingerprint,
            "recordStreak": graph.candidate.streak,
        },
        "aesthetics": {
            "balanceScore": graph.candidate.aesthetic,
            "selectionPolicy": "strict edge count, then temporal continuity, host continuity, visual balance",
        },
        "transition": transition,
        "geometry": geometry,
    }


def transition_for_pair(
    previous: GraphData | None,
    current: GraphData,
    pairs: list[tuple[int, int]] | None,
    removed: list[int] | None,
    added: list[int] | None,
    kind: str | None,
    mapping_cost: float | None,
) -> dict[str, object] | None:
    if previous is None or pairs is None or removed is None or added is None or kind is None:
        return None
    spawns = []
    for vertex in added:
        spawn, neighbors = spawn_point(current, vertex)
        spawns.append({
            "vertex": vertex,
            "position": [round_number(spawn.real), round_number(spawn.imag)],
            "neighbors": neighbors,
        })
    retained_edges = 0
    if kind == "growth":
        mapping = [target for _, target in sorted(pairs)]
        if not mapped_edges_preserved(previous.edges, current.edges, mapping):
            raise AssertionError(f"n={current.candidate.n}: growth did not retain all old edges")
        retained_edges = len(previous.edges)
    elif kind == "renewal":
        retained_edges, surviving_edges = retained_edge_count(previous.edges, current.edges, pairs)
        if retained_edges != surviving_edges:
            raise AssertionError(f"n={current.candidate.n}: renewal did not retain the surviving old edges")
    return {
        "from": previous.candidate.n,
        "kind": kind,
        "retainedPairs": [list(pair) for pair in pairs],
        "removedVertices": removed,
        "addedVertices": added,
        "spawns": spawns,
        # Keep the original scalar fields for consumers of growth records.
        "oldToNew": [target for _, target in sorted(pairs)] if not removed else None,
        "addedVertex": added[0] if len(added) == 1 else None,
        "spawn": spawns[0]["position"] if len(spawns) == 1 else None,
        "spawnNeighbors": spawns[0]["neighbors"] if len(spawns) == 1 else [],
        "retainedVertices": len(pairs),
        "retainedEdges": retained_edges,
        "mappingCost": None if mapping_cost is None else round(mapping_cost, 8),
        "animation": (
            {
                "durationMs": 900,
                "edgeFadeOutEnd": 0.0,
                "migrationStart": 0.0,
                "migrationEnd": 1.0,
                "edgeFadeInStart": 0.45,
            }
            if kind == "growth"
            else {
                "durationMs": 1350,
                "edgeFadeOutEnd": 0.20,
                "migrationStart": 0.16,
                "migrationEnd": 0.82,
                "edgeFadeInStart": 0.78,
            }
        ),
    }


def summary_from_record(record: dict[str, object]) -> dict[str, object]:
    host = dict(record["host"])
    transition = record.get("transition")
    optimality = dict(record["optimality"])
    n = int(record["n"])
    return {
        "n": n,
        "edges": int(record["edges"]),
        "averageDegree": float(record["averageDegree"]),
        "legend": str(record["legend"]),
        "host": str(host["label"]),
        "family": str(host["family"]),
        "record": f"data/records/{n:0{PAD_WIDTH}d}.json",
        "transition": None if transition is None else str(dict(transition)["kind"]),
        "optimality": str(optimality["status"]),
    }


def materialize_sequence(
    root: Path,
    sequence: Sequence[Candidate],
    runs: dict[str, RunArchive],
    *,
    pretty_json: bool,
    include_integer_coordinates: bool,
) -> list[dict[str, object]]:
    records_dir = root / "data" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    host_cache: dict[str, Host] = {}
    recorded_at = utc_now()
    summaries: list[dict[str, object]] = []
    previous_graph: GraphData | None = None
    previous_transform: Isometry | None = None

    for candidate in sequence:
        run = runs[candidate.run_id]
        graph = materialize_candidate(candidate, runs, host_cache)
        pairs: list[tuple[int, int]] | None = None
        removed: list[int] | None = None
        added: list[int] | None = None
        kind: str | None = None
        mapping_cost: float | None = None

        if previous_graph is None:
            transform = first_isometry(graph.raw_coordinates)
        elif candidate.run_id == previous_graph.candidate.run_id:
            assert previous_transform is not None
            transform = previous_transform
            pairs = [(index, index) for index in range(previous_graph.candidate.n)]
            removed = []
            added = [graph.candidate.n - 1]
            kind = "growth"
        else:
            assert previous_transform is not None
            aligned = exact_simple_alignment(previous_graph, graph, previous_transform)
            if aligned is not None:
                transform, mapping, added_vertex = aligned
                pairs = list(enumerate(mapping))
                removed = []
                added = [added_vertex]
                kind = "growth"
            else:
                transform, pairs, removed, added, mapping_cost, kind = choose_transmutation_alignment(previous_graph, graph)

        graph.coordinates = apply_isometry(graph.raw_coordinates, transform)
        transition = transition_for_pair(previous_graph, graph, pairs, removed, added, kind, mapping_cost)
        record = make_record(
            graph,
            run,
            transition,
            recorded_at,
            include_integer_coordinates=include_integer_coordinates,
        )
        filename = f"{candidate.n:0{PAD_WIDTH}d}.json"
        atomic_write_json(records_dir / filename, record, pretty=pretty_json)
        summaries.append(summary_from_record(record))
        previous_graph = graph
        previous_transform = transform

        if candidate.n <= 25 or candidate.n % 100 == 0 or candidate.n == len(sequence):
            label = "start" if kind is None else kind
            print(f"  {filename}: {candidate.edges:,} edges · {label} · {run.spec.label}", flush=True)

    keep = {f"{candidate.n:0{PAD_WIDTH}d}.json" for candidate in sequence}
    for path in records_dir.glob("[0-9][0-9][0-9][0-9].json"):
        if path.name not in keep:
            path.unlink()
    return summaries


def write_catalogs(root: Path, summaries: Sequence[dict[str, object]], sequence: Sequence[Candidate], *, publish: bool) -> None:
    generated_at = utc_now()
    catalog = {
        "schemaVersion": SCHEMA_VERSION,
        "generatorVersion": GENERATOR_VERSION,
        "generatedAt": generated_at,
        "maxN": len(summaries),
        "recordCount": len(summaries),
        "rendering": "dynamic canvas from JSON; no SVG assets",
        "records": list(summaries),
    }
    atomic_write_json(root / "data" / "catalog.local.json", catalog, pretty=True)
    if publish or not (root / "data" / "catalog.json").exists():
        atomic_write_json(root / "data" / "catalog.json", catalog, pretty=True)

    sequence_document = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "policy": [
            "strict record edge count",
            "maximum number of one-vertex growth transitions",
            "maximum new unit edges introduced by those growth transitions",
            "minimum host-family changes",
            "visual balance among remaining ties",
        ],
        "growthTransitions": sum(1 for summary in summaries if summary.get("transition") == "growth"),
        "growthEdgesIntroduced": sum(
            int(summaries[index]["edges"]) - int(summaries[index - 1]["edges"])
            for index in range(1, len(summaries))
            if summaries[index].get("transition") == "growth"
        ),
        "selection": [
            {
                "n": candidate.n,
                "edges": candidate.edges,
                "runId": candidate.run_id,
                "host": candidate.spec_key,
                "balanceScore": candidate.aesthetic,
                "recordStreak": candidate.streak,
            }
            for candidate in sequence
        ],
    }
    atomic_write_json(root / "data" / "sequence.json", sequence_document, pretty=True)


def prune_runs(root: Path, active_run_ids: set[str]) -> None:
    directory = root / "data" / "search" / "runs"
    if directory.exists():
        for path in directory.glob("*.json"):
            if path.stem not in active_run_ids:
                path.unlink()


def parse_host_names(value: str) -> set[str]:
    value = value.strip().lower()
    if value == "all":
        value = DEFAULT_HOSTS
    result = {item.strip() for item in value.split(",") if item.strip()}
    if not result:
        raise argparse.ArgumentTypeError("host list may not be empty")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-n", type=int, required=True, help=f"generate records through n (1..{MAX_N})")
    parser.add_argument("--restarts", type=int, default=8, help="new peeling runs per host")
    parser.add_argument("--seed", type=int, default=0, help="master seed for this batch")
    parser.add_argument(
        "--hosts",
        type=parse_host_names,
        default=parse_host_names(DEFAULT_HOSTS),
        help=f"comma-separated host groups; default: {DEFAULT_HOSTS}; or all",
    )
    parser.add_argument("--ties-per-n", type=int, default=12, help="record-count ties retained per n")
    parser.add_argument("--compactness-bias", type=float, default=0.30, help="prefer compact/symmetric survivors")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--pretty-json", action="store_true")
    parser.add_argument("--include-integer-coordinates", action="store_true")
    parser.add_argument("--publish", action="store_true", help="also replace data/catalog.json")
    parser.add_argument("--reset-search-state", action="store_true")
    parser.add_argument("--keep-all-runs", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.max_n <= MAX_N:
        parser.error(f"--max-n must be between 1 and {MAX_N}")
    if args.restarts < 1:
        parser.error("--restarts must be positive")
    if not 2 <= args.ties_per_n <= 64:
        parser.error("--ties-per-n must be between 2 and 64")
    if args.compactness_bias < 0:
        parser.error("--compactness-bias must be nonnegative")

    root = args.output.resolve()
    data_dir = root / "data"
    if args.reset_search_state and data_dir.exists():
        shutil.rmtree(data_dir)
    (data_dir / "search" / "runs").mkdir(parents=True, exist_ok=True)
    (data_dir / "records").mkdir(parents=True, exist_ok=True)

    legacy_graphs = data_dir / "graphs"
    if legacy_graphs.exists():
        print(f"warning: legacy SVG directory {legacy_graphs} is ignored", file=sys.stderr)

    existing_counts = load_existing_edge_counts(root, args.max_n)
    previous_sequence = load_previous_sequence(root)
    archived = load_runs(root)
    print(f"loaded {len(archived):,} archived record run(s)", flush=True)
    for run in make_exact_runs(args.max_n):
        archived.setdefault(run.id, run)

    specs = host_specs(args.max_n, args.hosts)
    print(f"searching n=1..{args.max_n} with {args.restarts} new restart(s) across {len(specs)} host(s)", flush=True)
    for host_index, spec in enumerate(specs):
        print(f"\n{spec.label}: building {spec.size:,}-vertex host ...", flush=True)
        host = build_host(spec)
        print(f"{spec.label}: {len(host.points):,} vertices, {sum(map(len, host.neighbors)) // 2:,} host edges", flush=True)
        for restart in range(args.restarts):
            run_seed = args.seed + 1_000_003 * host_index + 97_409 * restart
            rng = random.Random(run_seed)
            if restart == 0:
                choice_pool, degree_slack = 1, 0
            else:
                choice_pool = rng.choice((4, 6, 8, 12, 16, 24, 32))
                degree_slack = rng.choice((0, 1, 1, 1, 2))
            run = make_search_run(
                host,
                max_n=args.max_n,
                restart=restart + 1,
                run_seed=run_seed,
                choice_pool=choice_pool,
                degree_slack=degree_slack,
                compactness_bias=args.compactness_bias,
            )
            archived.setdefault(run.id, run)
            print(f"  restart {restart + 1:>3}/{args.restarts}: pool={choice_pool:<2} slack={degree_slack}", flush=True)
        del host

    all_runs = list(archived.values())
    best_edges = compute_best_edges(all_runs, existing_counts, args.max_n)
    layers = build_candidate_layers(all_runs, best_edges, args.max_n, args.ties_per_n, previous_sequence)
    active_ids = {candidate.run_id for layer in layers[1:] for candidate in layer}
    active_runs = [archived[run_id] for run_id in active_ids]
    save_runs(root, active_runs, pretty=args.pretty_json)
    if not args.keep_all_runs:
        prune_runs(root, active_ids)

    print("\nselecting the globally smooth strict-record sequence ...", flush=True)
    active_run_map = {run.id: run for run in active_runs}
    sequence = choose_sequence(layers, active_run_map, args.max_n)

    print("\nwriting JSON records ...", flush=True)
    summaries = materialize_sequence(
        root,
        sequence,
        active_run_map,
        pretty_json=args.pretty_json,
        include_integer_coordinates=args.include_integer_coordinates,
    )
    write_catalogs(root, summaries, sequence, publish=args.publish)
    print(f"\ncatalog: {root / 'data' / 'catalog.local.json'}", flush=True)
    print("rendering: browser Canvas from JSON (no SVG generated)", flush=True)


if __name__ == "__main__":
    main()
