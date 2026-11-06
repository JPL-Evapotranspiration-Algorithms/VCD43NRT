# VCD43NRT Migration Report

## Executive summary

The standalone `VCD43NRT` package is not a drop-in replacement for the legacy
built-in `VNP43IA4` and `VNP43MA3` clients. Those clients retrieve NASA's
precomputed VIIRS BRDF products; `VCD43NRT` instead fits BRDF parameters from
daily VIIRS surface-reflectance observations and generates its own NBAR, NDVI,
and albedo layers. Both paths expose NDVI and albedo operations on a date and
target geometry, but their temporal inputs, quality screening, processing,
dependencies, outputs, and cache layout differ. Numerical equivalence should
not be assumed.

There are three code paths to distinguish:

1. `VIIRS/VNP43IA4.py` and `VIIRS/VNP43MA3.py` are the direct-product clients
   used when the `use_VNP43NRT` option is disabled.
2. The package's existing `VNP43NRT` path is a separate near-real-time
   implementation using Julia, selected when `use_VNP43NRT` is enabled (the
   current default).
3. The adjacent standalone `VCD43NRT` candidate is a Python/Rust
   implementation. Its own legacy `VNP43NRT` names are deprecated aliases for
   `VCD43NRT`; they do not refer to the current Julia integration.

## Comparison

| Area | Built-in `VNP43IA4` / `VNP43MA3` | Standalone `VCD43NRT` | Migration implication |
|---|---|---|---|
| Input products | Retrieves `VNP43IA4` NBAR and `VNP43MA3` albedo granules from LP DAAC. | Retrieves daily `VNP09GA` surface reflectance and observation geometry, then estimates BRDF parameters. | Requires a new input-download and processing workflow; inputs and results cannot be substituted on a filename-only basis. |
| Temporal support | Uses the requested-date granule from the precomputed BRDF product. | For each output date, fits observations from date minus 16 days through the output date, inclusive (up to 17 daily dates). Missing observations more than four days old are skipped; a missing observation within the last four days raises an error. | More work and input data per output, with a different response to data latency and gaps. |
| NDVI | Reads NBAR I1 (red) and I2 (NIR) from `VNP43IA4`; masks mandatory-quality pixels unless QA application is disabled; calculates and clips NDVI to [-1, 1]. | Fits NBAR I1 and I2 from daily observations, then calculates and clips NDVI to [-1, 1]. The daily surface-reflectance reader applies its QF1 cloud-level mask by default. | The same index formula does not make the source reflectances or valid-pixel masks equivalent. |
| Albedo | Reads BSA and WSA from `VNP43MA3`, masks pixels unless the per-band mandatory QA value is exactly zero, applies the BRDF/AOT conversion, and uses the nine M bands to calculate broadband albedo. | Fits BSA and WSA for the nine M bands, applies the same broadband coefficients and AOT-based BRDF conversion, and clips the output to [0, 1]. Its target-date SZA is evaluated at 10:30 for albedo conversion. | The broadband recipe is closely related, but fitted parameters and source QA differ; the output is not expected to match the precomputed product exactly. |
| BRDF method | Uses BRDF parameters already present in NASA's products; the package does not fit a time series for this path. | Calls the compiled Rust `nrt_brdf_all` solver with weighted fitting enabled and scale 1.87. Produces WSA, BSA, NBAR, uncertainty, fit R², and observation count layers. The BRDF target SZA is evaluated at noon. | Requires validating fit behavior, solar-time conventions, uncertainties, and gap handling against the STARS use case. |
| Resolution and mosaicking | IA4 reflectance and MA3 albedo follow their product grids; client methods project granules to the requested geometry and mosaic tiles. | Uses a 2400-cell MODLAND grid for I bands and a 1200-cell grid for M bands; mosaics tile results to a requested geometry. | Check target cell size, reprojection/resampling, and edge behavior in end-to-end comparisons. |
| Missions and search | The built-in products are the SNPP `VNP` product family; the clients use the repository's LP-DAAC data-pool implementation. | Earthaccess/CMR-backed VIIRS inputs. SNPP is the default; the package documents JPSS-1/JPSS-2 selection and optional priority fallback. JPSS-2 CMR concept configuration may need an environment override. | New authentication, CMR availability, mission-selection, and operational monitoring considerations. Test fallback and identify mission in product provenance. |
| Runtime | Python, repository dependencies, and LP-DAAC access. | Python 3.11+, Earthdata access, and a compatible PyO3/Rust extension wheel; the package README says installed wheels do not require a Julia or Rust toolchain. | Add and pin the standalone package as a runtime dependency and verify a wheel exists for each deployment platform. |
| Storage and outputs | Separate download/product/mosaic directories for IA4 and MA3; GeoTIFF intermediates and caller-supplied mosaic paths. | Shared VIIRS download area, VNP09GA product cache, VCD43NRT staging area, and a VCD43NRT product directory. Granule layers are written as COGs and include NDVI, NBAR I1/I2, albedo, and BSA/WSA M-band layers; diagnostics add uncertainty/count and related fit layers. | Preserve old caches during migration, but do not assume they can satisfy the new workflow. Update output discovery and storage monitoring. |

## Important implementation details

### Product meaning and quality screening

The legacy IA4 client extracts the `Nadir_Reflectance_I1` and
`Nadir_Reflectance_I2` datasets, scales them, and applies the corresponding
mandatory BRDF quality dataset by retaining only QA value 0. The MA3 client
similarly extracts BSA/WSA M-band datasets and applies their mandatory
quality dataset. These are quality flags attached to precomputed BRDF
products.

The candidate begins instead with `VNP09GA` reflectance and its daily
observation angles. Its input reader masks cloud levels decoded from the QF1
cloud bits by default. A BRDF fit then uses the remaining observations across
the temporal window. This screening is not the same test as the legacy
mandatory-quality-equals-zero filter; valid-pixel coverage and edge cases
should be compared explicitly.

Both albedo paths use the same nine-band broadband coefficients and retrieve
GEOS-5 FP AOT for the directional conversion. However, the candidate first
estimates its own BSA/WSA, clips per-band converted albedo, and clips the
final broadband result. A shared formula therefore does not imply matching
albedo values.

### Candidate API and storage behavior

The candidate's primary API is `VCD43NRT`, with `NDVI(date_UTC, geometry)`,
`albedo(date_UTC, geometry)`, and tile-granule processing methods. Its output
granules carry `VCD43NRT_A<year><day-of-year>_<tile>` identifiers, with
mission suffixes in non-default/fallback cases. The package retains
`VNP43NRT` class, method, and directory aliases with deprecation warnings;
new integrations should use the `VCD43NRT` names.

The candidate has a cache-reuse issue worth resolving before operational
adoption: `VCD43NRT()` removes an existing granule output directory before
checking `granule.complete`. Consequently, an existing complete output is
regenerated rather than returned from cache. The staged daily input rasters
are reused when present, but output layers are rewritten.

### ECOv003-L2T-STARS integration status

The current STARS driver initializes either its local Julia-based
`VNP43NRT` connection or separate built-in `VNP43IA4` and `VNP43MA3`
connections. The standalone candidate is not listed as a dependency in the
STARS `pyproject.toml`, so it is not currently wired as an installable
replacement. Its API has analogous NDVI/albedo methods, but replacing the
connection also requires wiring its input directories, dependencies,
authentication, mission settings, and output/cache expectations into the
driver and source-retrieval path.

Do not silently map the legacy `use_VNP43NRT` switch to this candidate without
clarifying the behavior: it currently selects the local Julia path, not the
precomputed IA4/MA3 path. Prefer a clearly named option or a deliberate
adapter/migration that preserves the existing option's documented meaning.

## Recommended migration checks

1. Package and pin `VCD43NRT`; verify import and Rust-extension loading on each
   operational OS/Python target. Confirm Earthdata credentials and CMR access
   in the deployment environment.
2. Resolve the candidate output-cache deletion before relying on repeat-run
   reuse. Keep old IA4/MA3 and Julia output directories intact until rollback
   is no longer needed.
3. Run paired outputs for representative dates and tiles, including recent
   missing-data dates, older gaps, coast/edge tiles, and cloud-contaminated
   scenes. Compare NDVI/albedo values, valid-pixel coverage, grid alignment,
   and tile mosaics—not just file existence.
4. Validate that the 17-day window, cloud screening, daily gap policy, fitted
   BRDF parameters, target SZA times, and albedo clipping meet the expected
   STARS science and latency requirements.
5. Add regression tests for candidate dependency loading, API adaptation,
   output variable names, mission provenance/fallback, cache behavior, and
   reproducible mosaics before changing the production default.

## Sources reviewed

- STARS legacy clients: `ECOv003_L2T_STARS/VIIRS/VNP43IA4.py`,
  `ECOv003_L2T_STARS/VIIRS/VNP43MA3.py`, and
  `ECOv003_L2T_STARS/VIIRS/VIIRSDataPool.py`.
- STARS integration and alternate Julia path:
  `ECOv003_L2T_STARS/L2T_STARS.py`,
  `ECOv003_L2T_STARS/VNP43NRT/VNP43NRT.py`, and `pyproject.toml`.
- Standalone candidate reviewed from the adjacent `VCD43NRT` project:
  `VCD43NRT/VCD43NRT/VCD43NRT.py`,
  `VCD43NRT/VCD43NRT/VIIRS_granule.py`,
  `VCD43NRT/VCD43NRT/VIIRS.py`,
  `VCD43NRT/VCD43NRT/VIIRS_missions.py`,
  `VCD43NRT/README.md`, and `VCD43NRT/pyproject.toml`.
