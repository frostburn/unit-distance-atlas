# Unit-distance motion atlas

A JSON-only search pipeline and transparent Canvas frontend for exploring a
strict-record sequence of planar unit-distance graphs. No SVG files are emitted
or loaded. The browser turns exact graph coordinates and edge tables into a
continuously styled, animated scene.

The supplied project contains a demonstration atlas through `n = 200`. The
same generator supports `n = 2000`.

## What “strict” means

At every point count, the selected frame has the greatest edge count present in
the atlas search state. Temporal smoothness, host continuity, and visual balance
are used **only to choose among candidates tied at that edge count**. The
sequence optimizer is never allowed to trade away an edge for a prettier
transition.

The values through `n = 21` are supplied by known exact constructions and are
marked **proven optimal**. Larger frames are marked **strict atlas record**:
they are the best counts found by the current archived runs, not a claim of a
global proof.

## Quick start

```bash
python -m pip install -r requirements.txt
python serve.py --open
```

Use an HTTP server rather than opening `index.html` as a `file:` URL, because
the frontend loads JSON with `fetch()`.

To create a fresh small atlas:

```bash
python generate.py \
  --max-n 200 \
  --restarts 8 \
  --seed 1 \
  --reset-search-state \
  --publish
```

To search through the intended target:

```bash
python generate.py \
  --max-n 2000 \
  --restarts 16 \
  --seed 1 \
  --publish
```

A stronger incremental pass can reuse the saved lineages:

```bash
python generate.py \
  --max-n 2000 \
  --restarts 64 \
  --seed 1 \
  --publish
```

Using the same seed with a larger restart count reproduces the old run seeds
and adds the new ones. Existing record counts are floors: later searches cannot
replace a record with a lower edge count. Tied candidates may still change the
chosen movie sequence when they improve continuity or balance.

Use a different seed to add an independent batch:

```bash
python generate.py --max-n 2000 --restarts 32 --seed 20260810 --publish
```

## Browser controls

- **Play/Pause**, Previous, Next, direct point-count input, timeline scrubbing,
  and `0.5×` to `3×` playback speed.
- **Dark/light mode** changes the page behind the transparent Canvas.
- **Mouse wheel** zooms around the pointer; dragging pans; Reset restores the
  automatic framing.
- **Pinch zoom and two-finger pan** work on touch devices.
- **Left/Right arrows** step, **Space** plays or pauses, and **0** resets the
  camera.
- Hovering over a graph vertex isolates its neighbours and primary edges, with secondary edges and tertiary vertices shown in a contrasting color. Newly created edges glow amber during cell division.
- Hovering over the record chart loads and renders a live Canvas miniature;
  clicking the chart animates to that frame.

## Animation model

Every record after the first contains a `transition` object.

### Growth / cell division

When the strict record for `n + 1` contains the selected `n` graph:

1. old vertices keep their persistent identities and exact positions;
2. the newborn starts at the centroid of its final neighbours;
3. it follows a lightly overshooting spring to its exact unit-distance
   position;
4. its incident edges grow in while neighbour cells pulse.

The old edge set remains visible throughout.

Containment is checked before cell renewal, including between drawings from
different algebraic hosts. If one additional cell preserves every old edge,
the transition is always a single division; the spatial matcher is not allowed
to replace that division with unnecessary deaths.

### Cell renewal

When removing at most two cells leaves all edges between the surviving cells
present in the next record:

1. those retained cells and edges remain continuously visible;
2. up to two cells die with only their incident edges disappearing;
3. up to three replacement cells divide into the new drawing;
4. new edges grow in without blanking the graph.

Cell death is never used merely to improve a spatial assignment. More than half
of the old edge set must survive, and every edge whose endpoints survive must
still exist after mapping; otherwise the transition keeps every cell and uses
a transmutation.

### Transmutation

When neither growth nor structure-preserving renewal is possible:

1. the old edges fade completely;
2. every old cell migrates along a gently curved path;
3. the one additional cell appears during migration;
4. only near the end do the new record edges fade in.

Thus a host change reads as a deliberate reorganization rather than as a jump
or a false implication that the new graph contains the old one.

The generator aligns each new drawing with the previous frame before writing
JSON. Exact translations are used when possible. Otherwise, rotations,
reflections, and a spatial cell assignment are searched to reduce motion.

## Continuous visual scaling

The frontend has no visual breakpoints based on graph size. Node radius, edge
width, edge opacity, outline width, halo size, and glow are smooth functions of
`log(1 + n)`. This keeps early frames bold while preventing a 2,000-node frame
from becoming an opaque blob, without a visible style switch at an arbitrary
point count.

Camera framing also interpolates continuously between source and target bounds.
User pan and zoom are applied on top of that automatic camera.

## Search pipeline

The search operates in exact algebraic host graphs:

- triangular/hexagonal lattice;
- the rank-four Moser lattice with its 18 exact unit vectors;
- cyclotomic boxes for orders 12, 18, 24, 30, and 36;
- binary sections of cyclotomic orders 36 and 60.

A randomized low-degree peeling pass yields a complete nested lineage from one
host. Peeling includes a compactness bias, and each prefix receives a continuous
balance score based on isotropy, centering, and degree regularity.

For every `n`, the generator retains several tied best-count candidates. A
global dynamic-programming pass then chooses the strict-record movie path
lexicographically by:

1. number of genuine one-cell growth transitions;
2. number of new unit edges introduced by those growth transitions;
3. number of adjacent frames in the same host family;
4. accumulated visual-balance score;
5. accumulated record streak length.

The second rule favors visually meaningful divisions over symmetry in an
individual still. For example, when growth counts tie, it selects the
incomplete six-cell hexagon that divides directly into the filled seven-cell
hexagon rather than a more symmetric six-cell triforce that must transmute.

This is deliberately different from independently choosing the prettiest frame
at every `n`.

Useful options:

```text
--hosts hex,moser,z12,z18,z24,z30,z36,binary
--ties-per-n 12
--compactness-bias 0.30
--pretty-json
--include-integer-coordinates
--keep-all-runs
--reset-search-state
--publish
```

`--ties-per-n` controls how much freedom the sequence optimizer has. Larger
values use more disk and memory but can find smoother strict-record paths.

## Data layout

```text
data/
  catalog.json             published/default frontend catalog
  catalog.local.json       latest local catalog, preferred by the frontend
  sequence.json            selected strict-record lineage and diagnostics
  records/
    0001.json
    0002.json
    ...
    2000.json
  search/
    runs/                   reusable compact peeling lineages
```

There is intentionally no `data/graphs/` directory. A one-restart full-range
verification through `n = 2000` occupied about 176 MB uncompressed; repeated
coordinates and edge tables dominate that size. The browser lazy-loads records
and keeps only a 24-record LRU window, so playing the complete sequence does not
retain the entire atlas in memory.

A record contains:

```json
{
  "n": 1178,
  "edges": 0,
  "host": { "family": "moser" },
  "transition": {
    "kind": "growth",
    "retainedPairs": [[0, 0], [1, 1], [2, 2]],
    "removedVertices": [],
    "addedVertices": [1177],
    "spawns": [{"vertex": 1177, "position": [0.0, 0.0], "neighbors": [12, 47, 301]}]
  },
  "geometry": {
    "coordinates": [[0.0, 0.0]],
    "edges": [0, 1, 1, 2],
    "bounds": [0.0, 0.0, 1.0, 1.0]
  }
}
```

The example values above illustrate the schema rather than an actual record.
Edges are flattened pairs to reduce JSON overhead. Integer host coordinates are
omitted by default because the frontend does not need them; enable them with
`--include-integer-coordinates` for mathematical inspection.

## Repository workflow

A practical split is:

- commit `index.html`, `app.js`, `styles.css`, `generate.py`,
  `known_optima.py`, and `data/catalog.json`;
- decide how much generated `data/records/` belongs in the published site;
- normally keep `data/catalog.local.json` and `data/search/runs/` as local or
  large-file build state.

The frontend first tries `catalog.local.json`, then falls back to
`catalog.json`. `--publish` updates both.

## Tests

```bash
python -m unittest discover -s tests -v
node --check app.js
```

The tests verify the exact small counts, numerical unit lengths, flattened edge
counts, transition mappings, retained edges in growth steps, absence of SVG
output, and non-regression under incremental search.
