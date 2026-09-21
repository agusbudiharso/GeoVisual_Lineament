# GeoVisual Lineament v7.2.2 — Stable Release

This build preserves the **v7.1 Refined Gestalt LINE scientific detection core** and adds an experimental framework to test the technical effect of **Continuity-Gated Evidence Refinement (CGER)**.


## QGIS compatibility
This maintenance release keeps the v7.2 scientific/experimental logic unchanged and adds one-package compatibility for **QGIS 3.22+ (Qt5)** and **QGIS 4.x (Qt6)**. The compatibility layer handles QAction location, dialog execution, and QgsField scalar types without importing PyQt5 directly.

## Scientific core preserved
- scale factor S<1
- Gaussian 5x5 smoothing, sigma=0.8/S
- sub-sampling
- 2x2 gradient
- orientation-coherent 8-connected region growing
- inertia rectangle
- Helmholtz/a-contrario FAR
- 8 hillshade azimuths
- 3 scales around primary S
- cross-condition fusion
- local ATHR/DTHR linking

## Common-intermediate validation design
After fusion/linking, all candidates with length >= 0.70*Lmin are assigned a stable `candidate_id`. The exact same set is supplied to all experimental arms:

- **A0**: no CGER refinement
- **A1**: full v7.1 CGER
- **A2**: CGER without continuity rescue
- **A3**: CGER without AZ/SCALE repeatability rescue
- **A4**: CGER without FAR rescue
- **A5**: minimum-length-only baseline

A1 implements the v7.1 short-fragment rule:

`DELETE = short-below-protection AND isolated AND AZ_SUPPORT<=1 AND SCALE_SUPP<=1 AND FAR>0.20`

where protection length = `1.35 * Lmin`.

## Validation outputs
Each timestamped run folder includes:
- `common_intermediate_candidates.gpkg`
- `A0_candidates.gpkg` ... `A5_candidates.gpkg`
- `candidate_decision_audit.csv`
- `A0-A5_metrics.csv`
- `experiment_manifest.json` with SHA-256 input/plugin hashes
- normal v7.1-equivalent A1 outputs: All/Selected/Principal
- `Selected_MINAZ2_Sensitivity.gpkg`
- orientation and detection summaries

## Metrics automatically available without external reference
- retained/deleted counts
- short candidates (<1.35*Lmin)
- isolated/coherent short counts
- IFR
- CSR
- per-arm refinement runtime

Precision, recall, F1, fragmentation index and matched-length ratio are intentionally marked `NA_REFERENCE_REQUIRED` until an independent reference linework is supplied. The build does not fabricate validation accuracy.

## Important implementation audit notes
1. v7.1 Selected defaults to `MIN_AZ=1`, which makes the AZ_SUPPORT alternative permissive. v7.2 therefore also exports a **MIN_AZ=2 sensitivity** layer without silently altering A1.
2. With `continuity=min(1,hits/3)`, threshold 0.34 requires >=2 hits, while 0.67 requires >=3 hits. v7.2 preserves this behavior for reproducibility.

## Scientific caution
All outputs are **DEM-derived lineament candidates**, not proven faults. Geological, geophysical and/or field validation remains required.


## v7.2.2 stability changes
- Reads a capped-resolution DEM directly through GDAL instead of first allocating the full DEM array.
- Adds a conservative working-memory guard and high safety caps for pathological region/detection explosions.
- Yields UI events during long runs to reduce apparent freezes.
- Keeps QGIS 3.22–3.99 and QGIS 4.x metadata/Qt compatibility.
- Scientific extraction and A0–A5 validation logic is retained from v7.2.1.
