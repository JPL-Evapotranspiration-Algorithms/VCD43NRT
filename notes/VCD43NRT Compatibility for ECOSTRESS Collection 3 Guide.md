# VCD43NRT ECOSTRESS Drop-in Fix Report

## Objective and compatibility baseline

The target is to replace the ECOSTRESS pipeline's existing Julia-based
`VNP43NRT` connection with the standalone Python/Rust implementation while
keeping the existing NRT science workflow and ECOSTRESS-facing inputs and
outputs stable. The intended changes are:

1. Select among SNPP, JPSS-1, and JPSS-2 inputs.
2. Prefer SNPP and use JPSS when the preferred mission has no granule for a
   tile/date.
3. Rebrand the processor and products from `VNP43NRT` to `VCD43NRT`.
4. Expose a clear mission-selection interface.

This report uses the Julia `VNP43NRT` implementation as the workflow baseline.
The built-in `VNP43IA4` / `VNP43MA3` clients in ECOSTRESS are a different path:
they retrieve same-day precomputed BRDF products rather than fitting BRDF from
daily observations. If those clients are the intended baseline, switching to
the current candidate changes the science workflow and inputs, not merely the
mission support and branding. That choice should be settled before claiming
pixel-level drop-in equivalence.

## Required fixes

### P0 — Make mission selection and fallback correct by default

1. **Wire all three mission clients.** `VCD43NRT.__init__` selects `VJ109GA`
   only for JPSS-1 and sends both SNPP and JPSS-2 through `VNP09GA`. Select the
   appropriate source client for JPSS-2 as well, or use a mission-neutral
   client that resolves the correct product and granule class for every
   mission. Ensure the selected class, product directory, downloader, and HDF
   granule reader agree with the granule actually found.
2. **Turn on the requested SNPP-first fallback behavior.** Fallback is
   currently opt-in (`fallback_mode=False`), so the default processor stops
   when SNPP is missing. Make the ECOSTRESS/default policy try
   `SNPP -> JPSS1 -> JPSS2`. When a mission is explicitly selected, use it as
   the first priority; if fallback is enabled, try the remaining configured
   missions afterward. Today, `mission="JPSS1", fallback_mode=True` can still
   prioritize SNPP because the default priority list is fixed. Document and
   test precedence when both `mission` and `mission_priority` are supplied.
3. **Support JPSS-2 discovery without an undocumented setup step.** The
   JPSS-2 CMR concept ID has no built-in default. Provide a verified default,
   or fail at initialization/search with a clear actionable message that
   explains the required override. A user selecting JPSS-2 should not receive
   an empty listing that looks like ordinary data unavailability when the
   query is not configured.
4. **Select fallback consistently for every observation date and tile.** The
   BRDF lookback fetches a series of daily granules. For each tile/date,
   select the first available mission in priority order, then use that
   granule's matching reader and record its mission. Do not silently return a
   granule object configured for one mission when the downloaded file belongs
   to another. Test a window containing SNPP dates, JPSS-1 fallback dates, and
   JPSS-2 fallback dates.
5. **Preserve source provenance.** Fallback output identifiers currently use a
   generic `_JPSS` suffix, which does not distinguish JPSS-1 from JPSS-2 and
   cannot describe a window that used multiple missions. Record the selected
   mission per input date in output metadata or a manifest; include enough
   selection information in the output/cache identity to prevent a run with a
   different mission policy from reusing incompatible results.

### P0 — Preserve the existing ECOSTRESS call contract

6. **Match both downloader method signatures.** The established downloader
   interface accepts `date_UTC`, `geometry`, optional `filename`, and optional
   `resampling` for both `NDVI` and `albedo`. The candidate `NDVI` accepts
   these arguments, but `albedo` has `save_preview` instead of `resampling`.
   Add and honor `resampling` in `albedo`, keeping preview behavior optional
   without replacing a supported argument. Verify positional and keyword
   calls used by the pipeline.
7. **Preserve output geometry, mosaicking, and failure semantics.** Keep the
   requested target geometry, established mosaic fill order, optional file
   output, and explicit unavailable-data errors. `albedo` should handle a
   geometry intersecting no MODLAND tiles with a clear `ValueError`, as
   `NDVI` already does, rather than failing later while setting a colormap on
   `None`.
8. **Keep the old processing path stable while changing mission sources.**
   Regression-test the Julia `VNP43NRT` behavior against the Python/Rust
   candidate for the temporal window, daily-gap policy, cloud screening,
   angle inputs, fit settings, solar-noon target for BRDF, 10:30 target for
   albedo/AOT, band selection, albedo coefficients, clipping, and raster
   alignment. These algorithm and input-screening details affect results and
   valid-pixel coverage; changing them is a science/workflow change, not just
   rebranding. Where exact raster agreement is not expected, set and approve
   tolerances and coverage criteria.

### P1 — Fix caching and make re-runs safe

9. **Check completeness before deleting outputs.** `VCD43NRT()` currently
   removes an existing granule directory and only then tests
   `granule.complete`. The completeness check can therefore never return a
   valid cached granule. Return a complete compatible result first; otherwise
   regenerate using a temporary output directory and replace the old result
   only after successful completion. Do not ignore cleanup failures.
10. **Make cache validity mission-aware.** Include the source-selection
    policy and per-date mission provenance in cache validation. A complete
    output produced with a different mission sequence or algorithm settings
    must not be treated as a valid equivalent merely because the expected
    layer filenames exist.
11. **Complete and test the rebrand compatibility layer.** Use `VCD43NRT` for
    new classes, CLI commands, directories, and output identifiers. The
    candidate already has warning-producing `VNP43NRT` class, method, and
    directory aliases; retain them for a transition period and add tests that
    each alias produces the same result and mission policy as its new name.

### P1 — Package and operational readiness

12. **Expose one consistent mission interface.** Support
    `mission="SNPP"`, `"JPSS1"`, or `"JPSS2"` in the Python API and CLI, plus
    explicit fallback/priority controls. Validate invalid missions and
    priorities early; show the effective policy in logs and product
    provenance. Document whether fallback is on by default and how to pin a
    mission.
13. **Make the standalone package a reliable ECOSTRESS dependency.** Pin the
    package version in the pipeline environment and verify compatible
    Python/Rust wheels for every ECOSTRESS deployment platform. Fail early
    with an actionable diagnostic if the extension cannot load; do not
    silently fall back to another algorithm.
14. **Test real data discovery and product parity.** Retain unit tests for
    mission normalization and CMR queries, and add integration-level tests
    with mocked CMR responses and representative HDF5 fixtures for all three
    mission formats. Add paired end-to-end checks for SNPP-only, JPSS-1-only,
    JPSS-2-only, SNPP-to-JPSS fallback, no-data, missing recent observations,
    and repeat-run cache reuse.

## Acceptance criteria

- The default ECOSTRESS policy selects SNPP when available and otherwise
  chooses JPSS-1 then JPSS-2 for the same tile/date.
- Explicit mission selection works independently for all three missions;
  JPSS-2 is discoverable with the documented default configuration.
- Fallback in a multi-day BRDF window uses the correct reader for each daily
  granule, and the output identifies the missions actually used.
- Existing ECOSTRESS callers can invoke `NDVI` and `albedo` with the same
  date, geometry, filename, and resampling arguments as before.
- The candidate matches the selected NRT baseline for algorithm settings,
  raster geometry, layer names/meaning, and error behavior, within documented
  numerical tolerances.
- A complete compatible result is reused; incomplete or incompatible output
  is replaced safely without deleting a valid result before regeneration
  succeeds.
- No-data and unavailable dependencies fail clearly. No mission fallback,
  cache, or package-loading error is represented as a successful output.

## Reviewed implementation references

- Candidate processor and granule/cache behavior:
  `VCD43NRT/VCD43NRT.py`
- Candidate mission query, fallback, download, and granule dispatch:
  `VCD43NRT/VIIRS.py`, `VCD43NRT/VIIRS_missions.py`,
  `VCD43NRT/VJ109GA.py`, and `VCD43NRT/VJ209GA.py`
- Existing ECOSTRESS downloader contract:
  `ECOv003_L2T_STARS/VIIRS/VIIRSDownloader.py`
- Existing ECOSTRESS precomputed-product path:
  `ECOv003_L2T_STARS/VIIRS/VNP43IA4.py` and
  `ECOv003_L2T_STARS/VIIRS/VNP43MA3.py`
- Candidate installation and mission behavior:
  `pyproject.toml` and `README.md`
