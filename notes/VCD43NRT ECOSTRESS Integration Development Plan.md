# VCD43NRT Integration Development Plan for ECOSTRESS Collections 2 and 3

## Objective

Make the standalone VCD43NRT package the shared implementation of the
near-real-time VIIRS BRDF workflow used by ECOSTRESS Collections 2 and 3.
Replace their duplicated built-in NRT implementation without changing
historical SNPP results, and provide automatic JPSS fallback for new
processing when SNPP observations are unavailable.

This is a development plan, not a claim that the current Python/Rust
implementation already reproduces the existing Julia implementation exactly.
Exact reproduction is a release gate that must be demonstrated against
preserved historical inputs and reference outputs.

## Scope and baseline

- **In scope:** the locally integrated Julia-based `VNP43NRT` workflow and its
  shared daily VIIRS input, BRDF, NDVI, albedo, and mosaicking behavior in
  both collections; the package and integration work needed to replace it;
  and mission selection, fallback, provenance, and safe caching.
- **Separate existing workflow:** `VNP43IA4` and `VNP43MA3` read NASA's
  precomputed BRDF products. They are not equivalent to the daily-input NRT
  fitting workflow. Preserve this alternate path and its existing selection
  behavior unless a separate migration is explicitly approved. Do not remove
  its readers or shared dependencies simply because they are duplicated.
- **Historical baseline:** identify the actual Collection 2 and Collection 3
  NRT versions, configurations, source granules, ancillary inputs, and
  execution environments used for each reference case. Collection 2 may
  have used a separate Julia VIIRS retrieval helper; establish the actual
  path rather than assuming it.
- **Compatibility target:** preserve the existing collection-facing API,
  configuration, outputs, and errors unless an intentional change is
  documented and approved. The standalone package's deprecated `VNP43NRT`
  aliases are not the same implementation as the existing Julia wrapper.

## Reproducibility and mission policy

### Historical replay

The requirement is **exact reproduction**, not an approximate match or a
one-quantization-level tolerance. For each frozen reference case, require
identical decoded pixel values and masks for the relevant NRT layers and
downstream NDVI and albedo, along with identical dimensions, grid alignment,
coordinate reference system, and data types. Compare serialized files
byte-for-byte only where byte-identical containers are explicitly required;
compression and metadata can vary even when decoded scientific outputs are
identical. Define any additional downstream product comparisons before
certification.

Record and pin the source granule identities and versions, ancillary data,
algorithm parameters, dependency and solver versions, runtime platform,
resampling settings, and output encoding. Use a frozen source manifest for
replay so later archive reprocessing, missing inputs, or JPSS availability
cannot silently change the result. Missing pinned inputs must fail explicitly.
Do not infer a historical mode solely from the requested date.

If the Python/Rust solver or other parts of the new package cannot achieve
exact equality, investigate and correct the difference. If exactness remains
impossible, retain or encapsulate the legacy-compatible processing backend
behind the shared interface for historical replay; do not remove the old
implementation or claim completion until the requirement is met. Any
relaxation to numerical tolerances requires explicit scientific and
stakeholder approval and is not part of this plan's acceptance criteria.

### Operational fallback

For new ECOSTRESS NRT processing, configure the priority as
`SNPP -> JPSS1 -> JPSS2`. For **each observation tile and date** in the BRDF
lookback window, select the first available source in that order. A window
may therefore contain observations from more than one mission. Resolve and
record the actual product, granule, and mission for each selected date, and
use the corresponding reader and calibration/decoding rules.

Fallback is triggered by verified source unavailability, not by an
authentication, network, invalid configuration, corrupt granule, or
processing failure. Surface those failures explicitly rather than quietly
changing the source. If no mission has an observation, preserve and test the
existing NRT recent-versus-older gap policy. Define deterministic selection
when CMR reports multiple candidate granules and record the selected version.

Expose explicit mission-only and custom-priority settings, with documented
validation and precedence. Preserve the standalone package's current
SNPP-only default unless a separately approved public API change is made;
the ECOSTRESS integration can explicitly enable its new operational fallback
policy. Historical replay uses the frozen manifest, not an automatic fallback.

## Phase 1: Establish contracts and reference data

1. Inventory both collection drivers, `use_VNP43NRT` selection, constructor
   and directory arguments, callers, authentication, ancillary data, product
   naming, output formats, cache paths, exceptions, and dependencies. Identify
   precisely which code can be replaced without affecting the precomputed
   product path.
2. Record the current Julia and standalone processing steps side by side:
   temporal window and gap handling; input QA and cloud masks; geometry and
   resampling; solar angles; BRDF fit parameters and numerical behavior;
   bands, albedo/AOT conversion, clipping, mosaic order, and output encoding.
3. Capture reference runs for **each** collection using real archived SNPP
   inputs and expected outputs. Include different seasons, tiles, target
   geometries, multiple-tile mosaics, cloud and edge conditions, and relevant
   older/recent data gaps. Preserve or make reproducibly accessible the
   original source and ancillary granules, code, environment, and run
   configuration. Include downstream ECOSTRESS outputs where feasible.
4. Build a comparison harness that reports mask differences, exact value
   differences, grid and metadata differences, and first failing layers. Keep
   small representative fixtures available to automated tests and run larger
   archived cases in an appropriately provisioned environment.
5. Confirm operational product and collection identifiers, availability,
   access requirements, and representative HDF schemas for SNPP, JPSS-1,
   and JPSS-2 against authoritative current sources. Do not hard-code an
   unverified JPSS-2 CMR concept ID.

**Exit gate:** a documented API/science contract and reproducible reference
cases for both collections, with an explicit list of missing inputs or
unresolved historical configurations.

## Phase 2: Make VCD43NRT scientifically and operationally compatible

1. Compare the standalone SNPP-only workflow against the frozen Julia
   references. Resolve differences in input handling, fit behavior, solar
   timing, output values, masks, and raster geometry. Provide a
   legacy-compatible backend if necessary to meet exact replay.
2. Verify and correct mission-specific dispatch end to end: CMR query,
   downloader, file naming, product cache, HDF granule reader, and per-date
   processing. Support verified JPSS-1 and JPSS-2 product configurations;
   fail with an actionable diagnostic when a selected mission is not
   configured.
3. Implement and test deterministic per-tile/per-date fallback, explicit
   priority and mission-only modes, and the failure distinctions described
   above. Verify a mixed-mission lookback window rather than locking an
   entire window to one mission.
4. Match the collection downloader contract for `NDVI` and `albedo`,
   including `date_UTC`, target `geometry`, optional `filename`, and
   `resampling`, as well as positional and keyword call patterns. Preserve
   target geometry, mosaic fill order, optional file output, and documented
   failure behavior. Report a geometry with no intersecting tiles clearly.
5. Record a manifest for every output identifying the effective selection
   policy, each selected source granule and mission, versions or checksums,
   missing observations, ancillary inputs, algorithm and software versions,
   parameters, and relevant raster settings. A generic JPSS suffix alone is
   insufficient for a mixed-mission window.
6. Make caches compatible with replay and fallback. Validate cached results
   against the complete effective source manifest and processing
   configuration, not merely filenames. Define when live-source availability
   is re-resolved so a newly available preferred source cannot silently reuse
   an incompatible result. Reuse complete matching results; generate
   replacements in temporary locations and publish only after success,
   retaining valid prior results on failure.
7. Preserve and test supported `VNP43NRT` aliases and directory arguments
   during the migration. Verify package dependencies and Python/Rust wheels
   for each actual deployment platform; fail clearly if the extension or a
   required dependency cannot be loaded.

**Exit gate:** exact SNPP replay passes the frozen reference suite for both
collections; mission dispatch, fallback, API, cache, and error tests pass.
Any unresolved scientific difference blocks migration.

## Phase 3: Integrate both ECOSTRESS collections

1. Add and pin the validated VCD43NRT release in each collection's deployment
   environment. Verify installation and extension loading in the actual
   production images and Python versions.
2. Introduce the smallest necessary collection-specific adapters and wire the
   existing NRT selection through the shared package. Preserve the existing
   `use_VNP43NRT` meaning and the alternative precomputed-product path.
   Configure mission priority, replay manifest, directories, target
   resampling, ancillary services, and logging explicitly.
3. Respect Collection 2 and 3 authentication and exception contracts. Test
   unattended Earthdata access and credential handling without assuming the
   two collections have identical login behavior. Never log credentials.
4. Preserve downstream product names, layers, geometry, masks, and
   caller-visible outputs; retain old caches and the Julia route for rollback
   until the acceptance gates have passed. Remove only genuinely redundant
   NRT code and dependencies after checking all remaining callers and the
   separate precomputed-product path.
5. Document configuration, historical replay, fallback policy, manifest and
   cache behavior, installation, operational failure modes, and rollback in
   both collections and the standalone package.

**Exit gate:** each collection passes its own integration suite using the
shared package, and its previous NRT and precomputed-product selections
remain operational as intended.

## Phase 4: Validate and release

### Required test matrix

- **Historical exactness:** run each collection's frozen SNPP references
  through the old and new NRT paths; compare exact decoded values, masks,
  grids, and required downstream products. Repeat across supported
  production platforms or explicitly restrict the certified environment.
- **Source selection:** test SNPP present, JPSS-1 fallback, JPSS-2 fallback,
  explicit mission-only and custom priority, and mixed missions in one
  lookback window. Assert selected granule identities, mission-specific
  readers, and provenance.
- **No-data and failures:** test all sources unavailable, recent and older
  missing dates, invalid mission configuration, authentication and CMR
  failures, download errors, and corrupt input. Confirm errors are not
  mistaken for normal source absence.
- **Compatibility:** test both public methods with positional and keyword
  arguments, resampling, file output, multi-tile mosaics, target geometry,
  aliases, no-intersection geometry, and the independent precomputed path.
- **Cache and replay:** test compatible reuse, changed source or policy,
  newly available preferred observations, changed algorithm parameters,
  incomplete outputs, interrupted writes, and frozen replay with a missing
  pinned input.
- **Real data and operations:** use representative actual products from each
  supported mission in addition to mocked CMR responses and HDF fixtures;
  verify authentication, ancillary downloads, storage, and production
  deployment. Compare performance and resource use against the existing
  workflow and agree on operational limits before rollout.

Cross-collection comparisons are useful for shared behavior, but identical
results are only expected when inputs, processing configuration, geometry,
ancillary data, and environments are identical. Each collection must also
match its own historical baseline.

### Rollout and rollback

1. Run the package in shadow mode alongside each existing NRT path on frozen
   historical cases and representative operational cases.
2. Obtain science and operations sign-off on exact replay, mission behavior,
   provenance, resource usage, and deployment readiness.
3. Enable the new NRT integration in stages, monitor source selection, gap
   rates, failures, and output coverage, and keep a documented rollback to the
   previous implementation and caches.
4. Remove duplicated NRT code only after the agreed rollback period and
   evidence that no other callers depend on it. Do not remove the separate
   precomputed-product implementation as part of this migration.

## Completion criteria

- Both collections use the shared package for their NRT path without
  breaking existing callers or the alternate precomputed-product path.
- Frozen historical SNPP cases reproduce their respective legacy results
  exactly under the defined comparison contract; otherwise release is blocked.
- New ECOSTRESS processing selects SNPP first, then JPSS-1 and JPSS-2 per
  observation tile/date, with explicit handling of operational errors.
- Output provenance and cache identity support mixed-mission windows and
  reproducible replay.
- The test matrix, production-platform installation, operational performance,
  documentation, sign-off, and rollback checks are complete.

## Scheduling and dependencies

Use the earlier seven-week outline only as a **provisional planning
estimate**, not a commitment: exact numerical parity, availability of
historical source and ancillary inputs, and real JPSS product validation
are unresolved dependencies that may change the schedule. Phase 1 establishes
the evidence and should inform estimates for the remaining phases.

## Related analyses

- [Collection 3 NRT compatibility guide](./VCD43NRT%20Compatibility%20for%20ECOSTRESS%20Collection%203%20Guide.md)
- [Collection 3 integration and alternative-path analysis](./ECOSTRESS%20Collection%203%20Integration%20of%20VCD43NRT%20Package%20Guide.md.md)
- [Collection 2 and 3 VIIRS code differences](./VIIRS%20Code%20Differences%20Between%20ECOSTRESS%20Collection%202%20and%203%20STARS.md)
