# Unit-distance graph atlas

A local, static atlas for dense planar unit-distance graphs.

The Python generator searches exact cyclotomic host graphs, keeps the best result seen for each `n`, and writes:

```text
data/
  catalog.json          lightweight index used by the chart
  catalog.local.json    ignored index written by local generation
  graphs/0001.svg       transparent, title-free SVG artwork
  records/0001.json     caption, provenance, vertex table, and edge table
```

Filenames always use four digits because the supported range is `1 … 9999`.

## Install and generate

Only the generator has a third-party dependency:

```bash
python -m pip install sympy
python generate.py --max-n 200 --restarts 8
```

This searches every size from 1 through 200. A restart produces one nested family, so a single search pass contributes candidates for every `n`, rather than running 200 unrelated searches.

Generation writes its index to the Git-ignored `data/catalog.local.json`, using
that file on later runs and falling back to the published `data/catalog.json`
on the first run. Generated graph and record files above 120 points are also
ignored so local experiments do not expand the checked-in atlas accidentally.
When a search improves any record from 1 through 120, the generator also
refreshes the version-controlled `data/catalog.json`, restricted to that
published range. Searches that only affect larger graphs leave it untouched.

Available exact host families are:

```text
hex, z12, z18, z24, z30, binary
```

The default portfolio is `hex,z12,z18,z24,binary`. A larger run can be launched with:

```bash
python generate.py --max-n 9999 --restarts 16 --hosts all
```

A complete 9999-record atlas is inherently large: every SVG repeats its own vertices and edges, and every JSON record contains its own geometry table. Expect substantial runtime and disk use. Generate a smaller prefix first to check the presentation and search settings.

## Improve existing records

Rerun the same command with more restarts or another seed:

```bash
python generate.py --max-n 200 --restarts 64 --seed 20260809
```

For each `n`, the generator reads the existing edge count and replaces `NNNN.svg` and `NNNN.json` only when the new candidate has **strictly more edges**. Ties leave the existing files untouched.

To concentrate on a suffix while preserving smaller records:

```bash
python generate.py --min-n 500 --max-n 1000 --restarts 64
```

Per-record JSON is compact by default. Add `--pretty-json` when human-readable indentation matters more than disk space.

## Open the frontend

Browser security normally blocks `fetch()` from `file://` pages, so use the included tiny server:

```bash
python serve.py --open
```

Or:

```bash
python -m http.server 8000
```

Then open `http://127.0.0.1:8000/`.

The frontend provides:

- dark mode by default and a light/dark toggle
- previous/next buttons, point-count input, and left/right arrow keys
- a hover-preview chart; clicking a chart position selects that graph
- wheel zoom around the pointer
- drag panning
- pinch zoom and two-finger panning on touch devices
- direct links to the selected SVG and metadata JSON

## Record schema

Each `data/records/NNNN.json` contains summary fields plus:

```json
{
  "host": {
    "cyclotomicOrder": 24,
    "basisDimension": 8
  },
  "search": {
    "restart": 3,
    "runSeed": 194818,
    "choicePool": 12,
    "degreeSlack": 1
  },
  "geometry": {
    "vertexTable": {
      "columns": ["id", "c0", "...", "x", "y"],
      "rows": []
    },
    "edgeTable": {
      "columns": ["source", "target"],
      "rows": []
    }
  }
}
```

The coefficient columns describe exact cyclotomic integers. The `x,y` columns are numerical values of the selected complex embedding and are used only for display.
