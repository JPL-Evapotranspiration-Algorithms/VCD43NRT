# `VCD43NRT` Development Plan: Exception Consolidation, Concurrency Safety & Download Hardening

**Date:** 2026-11-16

**Target Repository:** `VCD43NRT` (`1.4.0`)

**Coordinated Downstream Consumer:** `ECOv003-L2T-STARS` (`1.16.0`, [Issue #90](https://github.com/ECOSTRESS-Collection-3/ECOv003-L2T-STARS/issues/90))

## 1. Objectives & Architectural Constraints

1. **Zero ECOSTRESS Coupling:** `VCD43NRT` is a general-purpose VIIRS NRT BRDF, albedo, and NDVI library used across ECOSTRESS, SBG, and standalone workflows. It must **never** import `ECOv003_exit_codes` or encode ECOSTRESS-specific scheduling windows (such as `VIIRS_GIVEUP_DAYS`).

2. **Explicit Domain Exceptions:** Centralize all `VCD43NRT` domain exceptions under a unified `VCD43NRTError` hierarchy in `VCD43NRT/exceptions.py` while preserving backwards-compatible imports across `VCD43NRT/__init__.py`, `VCD43NRT/VIIRS.py`, and `VCD43NRT/VCD43NRT.py`.

3. **Multi-Worker Concurrency Safety:** Eliminate shared-directory race conditions when parallel `ECOv003-L2T-STARS` tile jobs share a common `<sources_directory>` and process overlapping MODLAND tiles/dates simultaneously.

4. **Download & Numerical Hardening:** Validate downloaded HDF5 granules before promoting them into cache, prevent `finally` cleanup errors from masking download exceptions, and guard against all-`NaN` mosaics or singular BRDF solver errors.

## 2. Workstream 1: Exception Consolidation & Solver Error Normalization

### 2.1 Centralize Exception Hierarchy (`VCD43NRT/exceptions.py`)

Consolidate the exception classes currently split across `VCD43NRT/exceptions.py`, `VCD43NRT/VIIRS.py`, and `VCD43NRT/VCD43NRT.py` into `VCD43NRT/exceptions.py`:

```
class VCD43NRTError(Exception):
    """Base exception for all VCD43NRT domain errors."""


class CMRServerUnreachable(VCD43NRTError):
    """Remote NASA CMR or Earthdata authentication service cannot be reached."""


class DownloadFailed(VCD43NRTError, ConnectionError):
    """Granule or ancillary transfer failed after retries, or downloaded file is unreadable."""


class AuxiliaryDownloadFailed(DownloadFailed):
    """Required ancillary meteorological/aerosol input (e.g., GEOS-5 FP AOT) failed to download."""


class VIIRSUnavailableError(VCD43NRTError):
    """CMR query succeeded, but no VIIRS granule exists for the requested tile, date, and mission(s)."""


class BRDFRetrievalFailed(VCD43NRTError, RuntimeError):
    """BRDF kernel inversion could not be completed (e.g., <7 valid observations or singular fit)."""

```

* Re-export all six classes in `VCD43NRT/__init__.py`, `VCD43NRT/VIIRS.py`, and `VCD43NRT/VCD43NRT.py` so existing `from VCD43NRT.VIIRS import DownloadFailed` and `from VCD43NRT.VCD43NRT import BRDFRetrievalFailed` imports remain 100% compatible.

### 2.2 Normalize BRDF Inversion & Mosaic Failures (`VCD43NRT/VCD43NRT.py`)

1. **Wrap singular Rust solver errors in `BRDFRetrievalFailed`:**

   * In `VCD43NRT.BRDF_parameters()`, `nrt_brdf_all(...)` raises `ValueError("singular BRDF fit for pixel ...")` when viewing geometry is degenerate. Catch `ValueError` around `nrt_brdf_all(...)` and raise `BRDFRetrievalFailed` so callers can distinguish degenerate geometry from invalid arguments.

2. **Reject all-`NaN` mosaics in `NDVI()` and `albedo()`:**

   * Before writing or returning the mosaicked raster in `VCD43NRT.NDVI()` and `VCD43NRT.albedo()`, check `if np.all(np.isnan(NDVI)):` / `if np.all(np.isnan(albedo)):` and raise `BRDFRetrievalFailed` instead of returning an empty raster.

## 3. Workstream 2: Shared-Storage Concurrency Safety

### 3.1 Atomic Granule Directory Promotion (`VCD43NRT/VCD43NRT.py`)

* **Current issue:** In `VCD43NRT.VCD43NRT()`, if `exists(directory)` is true, it immediately calls `shutil.rmtree(directory)` and writes layers directly into `directory`. Note that `if granule.complete:` is currently placed *after* `shutil.rmtree(directory)`, making it unreachable and causing concurrent workers to delete each other's in-progress or completed granules.

* **Fix:**

  1. Check `if granule.complete: return granule` **first**, before touching disk.

  2. Stage all output layers (`NBAR_I*`, `NDVI`, `WSA_M*`, `BSA_M*`, `albedo`, diagnostics) inside a PID/UUID-scoped temporary directory on the same filesystem (`<directory>.<pid>.<uuid>.tmp`).

  3. Once all layers are written and verified, atomically promote the staging directory into `directory` (e.g., if `directory` already appeared from a peer worker and `VCD43NRTGranule(directory).complete` is true, discard the temp directory; otherwise remove any incomplete directory and `os.replace(temp_directory, directory)`).

### 3.2 Atomic Staging GeoTIFF Writes (`VCD43NRT/VCD43NRT.py` & `VCD43NRT/VIIRS_granule.py`)

* **Current issue:** Writing directly to `VCD43NRT_staging/<tile>/<variable>/<date>_<var>.tif` or `VIIRS_products/...` allows concurrent processes to read half-written GeoTIFFs or collide on temporary files.

* **Fix:** Add a small helper `_atomic_write_geotiff(raster: Raster, filename: str, use_cog: bool = False)` that writes to `<filename>.<pid>.<uuid>.tmp.tif` and atomically renames via `os.replace(...)`.

### 3.3 Safe Temp Directory Cleanup (`VCD43NRT/VIIRS.py`)

* **Current issue:** In `VIIRS.download_granules()`, the `finally:` block executes `Path(temporary_download_directory).rmdir()`, which raises `OSError: Directory not empty` if `earthaccess.download` leaves behind a partial file on failure, masking the original exception.

* **Fix:** Replace `Path(temporary_download_directory).rmdir()` with `shutil.rmtree(temporary_download_directory, ignore_errors=True)`.

## 4. Workstream 3: Download Validation & Edge-Case Hardening

### 4.1 Validate Downloaded HDF5 Granules Before Caching (`VCD43NRT/VIIRS.py`)

* In `VIIRS.download_granules()`, before moving `download_file_path` into `self.download_directory`:

  1. Verify `download_file_path.exists()` and `download_file_path.stat().st_size > 0`.

  2. Open with `h5py.File(download_file_path, "r")` to verify the HDF5 superblock and root group are readable.

  3. If validation fails, unlink the corrupt temporary file, treat the granule as a failed attempt (`download_exception`), and allow the `RETRIES` loop to re-download it cleanly.

* Include the underlying `last_download_exception` details in `raise DownloadFailed(f"Error when downloading VIIRS files: {last_download_exception}") from last_download_exception`.

### 4.2 Clean Edge-Case Handling in `BRDF.py` and `VIIRS_granule.py`

* **`VCD43NRT/BRDF.py`:** Guard `np.nanmean(AOT)` and `np.nanmean(...)` on `SZA` with explicit `np.all(np.isnan(...))` checks (falling back to default `AOT = 0.1` and `SZA = 45.0`) instead of relying on `warnings.filterwarnings('ignore')`.

* **`VCD43NRT/VIIRS_granule.py`:** In `get_NDVI()`, guard `(nir - red) / (nir + red)` against zero denominators (`np.where((nir + red) == 0, np.nan, ...)`).

## 5. Unit Test & Verification Plan

1. **Add `tests/test_exceptions.py`:**

   * Verify inheritance hierarchy (`VCD43NRTError`, `CMRServerUnreachable`, `DownloadFailed`, `AuxiliaryDownloadFailed`, `VIIRSUnavailableError`, `BRDFRetrievalFailed`).

   * Verify backwards-compatible imports from `VCD43NRT`, `VCD43NRT.exceptions`, `VCD43NRT.VIIRS`, and `VCD43NRT.VCD43NRT`.

   * Verify `BRDF_parameters()` wraps singular solver `ValueError` in `BRDFRetrievalFailed` and `NDVI()` / `albedo()` raise `BRDFRetrievalFailed` on all-`NaN` outputs.

2. **Add `tests/test_concurrency_and_downloads.py`:**

   * Test that `VCD43NRT.VCD43NRT()` returns immediately without re-running when `granule.complete` is already `True`, and stages atomically via a temp directory when generating a new granule.

   * Test that `VIIRS.download_granules()` rejects corrupt/empty `.h5` files, retries, and cleans up `temporary_download_directory` without raising `OSError` when files remain on failure.

3. **Run Full Verification:**

   ```
   make test
   make smoke-test
   
   ```