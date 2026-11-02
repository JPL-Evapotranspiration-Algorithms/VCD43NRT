# Rust Update Plan

Replace the Julia-backed BRDF processing path with a Rust implementation exposed to Python through PyO3. Keep Python responsible for data discovery, raster preparation, geospatial metadata, and output writing; use Rust for the array-based BRDF calculations. Publish platform-specific wheels to PyPI so users on supported platforms can install and run the package with `pip install VCD43NRT` without installing Julia, Rust, or a Rust build environment.

## Objectives

- Replace the Julia subprocess solver with a directly callable Rust extension module.
- Preserve the current BRDF algorithm and externally visible output behavior.
- Keep GeoTIFF I/O and raster geometry handling in the existing Python raster stack for the first implementation.
- Publish wheels for declared Python versions and supported operating systems/architectures.
- Ensure normal installs on supported platforms use a prebuilt wheel and do not require Rust or Julia.

## Decisions

- Use PyO3 for Python bindings and maturin as the Python/Rust build backend.
- Keep the public workflow and product APIs in Python.
- Pass numerical arrays into Rust and return numerical results to Python; do not make Rust read or write GeoTIFFs in the initial migration.
- Remove the Julia runtime requirement and Julia environment initialization after the Rust path is validated.
- Support only the Python versions and platform/architecture combinations for which release wheels are built and tested. A source build may require a Rust toolchain and is not the promised default installation path.
- Build and validate the Rust extension before removing the Julia implementation so it remains available as a parity reference during development.

## Current Processing Boundary

`VNP43NRT.BRDF_parameters` currently stages reflectance, solar zenith, sensor zenith, and relative azimuth GeoTIFFs in Python. `process_VNP43NRT.jl` reads and stacks those files, calls `NRT_BRDF_all` from `src/VNP43NRT.jl`, then writes nine output GeoTIFFs. Python reopens those outputs as rasters.

The replacement boundary should be:

1. Python loads the staged raster stacks and solar-noon raster, preserving each raster's geometry and nodata interpretation.
2. Python converts the stacks to the agreed NumPy shape and dtype and calls the PyO3 function.
3. Rust computes the per-pixel BRDF results and returns a two-dimensional NumPy array (or a documented typed result structure).
4. Python maps result columns to output layers, applies the existing output handling, and saves rasters with the correct geometry.

Avoid intermediate solver output GeoTIFFs. Preserve staging GeoTIFFs if they remain useful as a cache for input preparation.

## Implementation Phases

### Phase 1: Define the behavioral and array contract

1. Document the solver inputs:
   - `Y`: reflectance, shaped `(pixel_count, observation_count)`.
   - `sz`, `vz`, `rz`: solar zenith, sensor zenith, and relative azimuth with the same shape and observation order as `Y`.
   - `soz_noon`: solar-noon zenith, shaped `(pixel_count,)`.
   - `weighted` and `scale`: defaults currently used by the Julia handler are `true` and `1.87`.
2. Define input requirements for dtype, dimensionality, contiguity, finite values, and NaN/nodata behavior. Ensure Python uses one deterministic observation ordering for all four stacks; sorting each directory independently is not sufficient if file sets can differ.
3. Document the result contract: shape `(pixel_count, 9)`, column order `WSA, BSA, NBAR, WSA_SE, BSA_SE, NBAR_SE, BRDF_SE, BRDF_R2, count`, and NaN results for pixels with fewer than seven valid observations.
4. Capture representative baseline fixtures and outputs from the Julia solver, including insufficient observations, missing values, weighted/unweighted mode, and numerically sensitive cases.

### Phase 2: Build the Rust solver

1. Add a Rust crate for the BRDF numerical implementation, with explicit module boundaries for RossThick/LiSparseR kernels, per-pixel weighted least squares, uncertainty/statistics, and batched processing.
2. Port the active `NRT_BRDF_all` path and only the helpers it calls. Do not port unused Julia routines unless a caller or test demonstrates they are required.
3. Preserve the current constants, equations, valid-observation filtering, weighting order, minimum sample count, standard-error and R² definitions, output column order, and float precision as closely as practical.
4. Avoid explicit matrix inversion where a stable linear solve can produce equivalent results; compare against baseline fixtures and document any justified numerical tolerance or behavior change.
5. Handle singular or ill-conditioned systems and invalid numeric inputs explicitly. Do not silently substitute plausible output values for solver failures.
6. Add Rust unit tests for the kernels, fit calculations, missing-data behavior, and output layout.

### Phase 3: Add PyO3 bindings

1. Expose a small Python-facing function such as `nrt_brdf_all(Y, sz, vz, rz, soz_noon, weighted=True, scale=1.87)`.
2. Use PyO3 with NumPy interoperability (`numpy` crate / `PyReadonlyArray` and `PyArray`) so inputs and outputs cross the boundary as arrays rather than serialized files.
3. Validate array rank, matching dimensions, solar-noon length, supported dtypes, and scale before computation. Return Python exceptions with actionable messages for invalid inputs.
4. Keep ownership and memory use explicit. Accept read-only arrays, avoid unnecessary copies when the input layout permits, and return a newly allocated result array.
5. Release the Python GIL during the CPU-bound Rust computation after obtaining validated array access, allowing other Python threads to run.
6. Add Python-level tests for bindings, validation errors, result shape/dtype, NaN handling, and parity with the baseline fixtures.

### Phase 4: Integrate into the Python processing workflow

1. Replace `process_julia_BRDF` with a Python helper that loads/validates input rasters, calls the Rust extension, maps output columns to names, and writes rasters using the established raster library.
2. Verify raster geometry, coordinate reference system, dimensions, nodata behavior, output filenames, and output dtype are preserved. Apply any existing filtering of physically invalid WSA/BSA/NBAR values at the same stage as before.
3. Ensure all per-date input layers correspond to the same observations. Detect missing, extra, or mismatched staged files rather than silently stacking inconsistent sequences.
4. Preserve progress/logging and failure behavior sufficiently for users to diagnose processing errors.
5. Remove the Julia subprocess, environment sanitization, `initialize_julia` option, Julia setup functions, and Julia-specific CLI arguments once the direct path is covered by tests.

### Phase 5: Configure wheel-based packaging

1. Change the build backend in `pyproject.toml` to maturin and configure the Python package/module name, Rust crate path, and extension-module settings.
2. Keep Python runtime dependencies in project metadata; remove Julia project files from package data and release instructions.
3. Build wheels in CI for each supported Python ABI and platform/architecture combination. Select the matrix based on the project's intended support policy, including Linux (manylinux), macOS (Intel and Apple Silicon as applicable), and Windows if supported.
4. Use a maintained wheel-building workflow (for example, maturin's GitHub Action) and test each built wheel in a clean environment with no Rust or Julia installed.
5. Build and test a source distribution as well. Document that source installs may need Rust and that the no-toolchain installation guarantee applies to platforms with published compatible wheels.
6. Publish release wheels and source distributions to PyPI, preferably through GitHub Actions trusted publishing, and verify that PyPI contains all expected artifacts before announcing a release.
7. Test actual `pip install VCD43NRT` resolution for representative supported Python/platform combinations, confirming pip selects a wheel rather than attempting a local Rust build.

### Phase 6: Remove Julia and update user documentation

1. Delete the Julia solver, Julia handler script, `Project.toml`, `Manifest.toml`, and Julia initialization helpers after parity and integration tests pass.
2. Remove Julia package data configuration and stale Julia-related imports, comments, and `initialize_julia` CLI or constructor options.
3. Update the README prerequisites and installation guidance to state that Julia and Rust are not needed when installing a compatible published wheel.
4. State the supported Python versions/platforms and explain that unsupported platforms or source builds may require Rust.
5. Retain the EarthData authentication and other Python runtime prerequisites in the installation/use documentation.
6. Update examples and tests so no supported workflow invokes Julia.

## Relevant Files

- `pyproject.toml` — build backend, maturin configuration, runtime dependencies, and packaged files.
- `VCD43NRT/VNP43NRT.py` — current Julia process launcher, input staging, and output integration.
- `VCD43NRT/process_VNP43NRT.jl` — current raster stack loading and output writing to be replaced.
- `VCD43NRT/src/VNP43NRT.jl` — BRDF algorithms and helper functions used as the porting/parity reference.
- `VCD43NRT/Project.toml` and `VCD43NRT/Manifest.toml` — Julia environment to remove after migration.
- `VCD43NRT/instantiate.py` and `VCD43NRT/instantiate.jl` — Julia setup helpers to remove after migration.
- `README.md` — prerequisites, installation, and processing guidance.
- `examples/generate_single_VNP43NRT_granule.py` — remove Julia-specific option and document the new workflow.
- `tests/` — Rust solver, Python binding, integration, and packaging verification.

## Verification Checklist

- Rust unit tests cover kernel calculations, weighted and unweighted fits, missing observations, insufficient observations, and result-column mapping.
- Rust outputs match Julia baseline fixtures within documented tolerances across representative pixel inputs.
- Python binding tests validate array dimensions, dtype/layout handling, invalid inputs, NaNs, output shape, and output dtype.
- Integration tests verify that Python writes all nine expected output rasters with correct values, shape, CRS, geotransform, and nodata behavior.
- Input file pairing/order is deterministic and mismatched date stacks fail clearly.
- Existing Python tests and a representative end-to-end processing workflow pass without Julia installed.
- Built wheels install and import in clean environments without Rust or Julia on every declared supported Python/platform combination.
- `pip install VCD43NRT` on supported combinations selects a compatible wheel; source installation requirements are documented.
- No production code, package metadata, examples, or documentation retain an active Julia dependency.

## Risks and Mitigations

- **Numerical drift during the port:** retain Julia as a temporary oracle and compare fixed fixtures before switching the default path.
- **Large array copies or memory pressure:** define contiguous layouts, profile representative 1,200- and 2,400-cell tiles, and avoid unnecessary Python/Rust copies.
- **Mismatched observation stacks:** construct a shared observation manifest keyed by date and validate every input variable against it.
- **Wheel gaps:** declare support only for tested combinations and make CI fail if any expected wheel is missing or cannot install in a clean environment.
- **Native geospatial dependency complexity:** keep raster I/O in Python for this migration; do not add Rust GDAL integration to the initial scope.
- **Source builds requiring Rust:** make the wheel-based installation guarantee explicit and publish wheels before recommending the new release.
- **Changing public API by removing `initialize_julia`:** search all callers and examples, remove the option consistently, and call out any compatibility impact in release notes.

## Out of Scope

- Replacing Python raster handling or adding GDAL bindings to Rust.
- Rewriting EarthData download, product access, or unrelated Python processing code.
- Changing BRDF equations or scientific interpretation except for a separately reviewed and tested correction.
- Guaranteeing `pip install` without compilation on platforms that do not have a published compatible wheel.
