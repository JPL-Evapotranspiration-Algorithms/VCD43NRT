# VIIRS Code Differences Between ECOSTRESS Collection 2 and 3 STARS

## Summary

The Collection 2 and Collection 3 Python VIIRS product code is largely shared
in behavior: the downloader interfaces, LP DAAC data-pool implementation, and
the `VNP43IA4` reader are identical in the checked-out trees. The `VNP09GA`
reader also retains the same CMR query, granule download flow, raster
decoding, cloud mask, and tile geometries. The meaningful differences are
mostly integration and operational details: Earthdata login and exception
ownership, a few CMR result-handling fixes, and the way the near-real-time
`VNP43NRT` wrapper starts Julia.

No evidence in these VIIRS reader changes indicates a change to the underlying
VIIRS product interpretation or science formulas. This is a source comparison,
not a pixel-level equivalence test.

## Comparison

| Area | Collection 2 | Collection 3 | Effect |
|---|---|---|---|
| Shared downloader and data pool | `VIIRSDownloader.py` and `VIIRSDataPool.py` | Same implementations | The interface (`albedo` / `NDVI`) and LP DAAC search, filename parsing, download layout, and raster grid construction are unchanged. |
| `VNP43IA4` | NBAR bands use scale 0.0001; mandatory QA retains only value 0; NDVI uses red I1 and NIR I2 and is clipped to [-1, 1]. | Same implementation | No observed change to reflectance decoding, QA filtering, NDVI, or mosaicking behavior. |
| `VNP43MA3` | Uses `geos5fp.GEOS5FP`; its granule constructor can pass working, download, and product directories to that connection. | Imports `GEOS5FP.GEOS5FP`; the granule constructor passes only the download directory when it creates the connection. | Albedo bands, QA filtering, BRDF/AOT conversion, and broadband coefficients are unchanged. The ancillary-data dependency's import path and configuration contract differ. |
| `VNP09GA` inputs | CMR query uses concept `C2631841556-LPCLOUD`, filters dates/geometry/tile, and downloads Earthaccess granules. | Same query and download behavior. | Same VIIRS daily source product and discovery logic. |
| `VNP09GA` decoding | Reads the same HDF datasets, uses the same fill-value/scale handling and I/M grids, and masks cloud levels decoded as `(QF1 >> 2) & 3`, with nonzero cloud levels excluded. | Same implementation. | No observed change to input pixels, cloud screening, resolution, or resampling logic. |
| Earthdata login | Private `VIIRS_CMR_LOGIN.py` checks `.netrc` first and only tries environment credentials if `.netrc` has no matching entry; a missing or malformed `.netrc` is wrapped as `CMRServerUnreachable` before the environment fallback. | Uses shared `ECOv003_L2T_STARS.login.login()`: environment credentials first, `.netrc` second, then interactive login. `SKIP_EARTHDATA_LOGIN` can provide a mock auth object. | Authentication precedence and headless/test behavior change. C2's helper does not provide C3's interactive or explicit test-skip path. |
| CMR bookkeeping | Initializes an empty granule table from empty dictionaries, accesses the date column by attribute, and warns whenever a nonempty result is added. | Declares the empty table's columns explicitly, uses bracket column access, and warns only when more than one granule is returned. | C3 handles empty-table shape and pandas access more explicitly; the warning now indicates multiple results rather than any result. The filename regex was also made a raw string with the same matching pattern. |
| NRT Julia wrapper | Always instantiates the Julia project before processing; invokes Julia through a shell command string. | Adds `initialize_julia` (default `False`), uses the package root as the Julia project, builds an argument list, and passes a copied environment with `JULIA_NUM_THREADS` set and GDAL variables removed. | Collection 3 changes setup responsibility and subprocess environment. Callers must initialize the Julia environment when needed; the VIIRS input reader itself remains the same. |
| Extra Julia VIIRS helper | Includes `STARS_jl/src/VIIRS.jl`, which lists LP DAAC URLs and downloads granules with `wget` and username/password arguments. | No corresponding `VIIRS.jl` file appears in the Collection 3 tree; its Python `VNP09GA` path uses Earthaccess/CMR. | Collection 2 has an additional Julia-side retrieval route; do not confuse it with the Python CMR implementation that exists in both collections. |

## Detailed notes

### Same product semantics in the Python path

`VIIRSDownloader.py`, `VIIRSDataPool.py`, `VNP43IA4.py`, and `VIIRS/__init__.py`
are identical between the checked-out repositories. `VNP43MA3.py` differs only
in the GEOS5FP import and the optional connection arguments described above.
Thus, the built-in IA4 and MA3 paths continue to consume the same precomputed
VIIRS BRDF products. In particular, their mandatory-quality masking remains
an exact `QA == 0` test; it is not the daily surface-reflectance cloud test.

The daily VNP09GA path also retains the same core data rules. It queries the
same CMR concept, parses the same `VNP09GA` version-002 HDF5 filename shape,
uses the same HDF fields and fill-value handling, and applies the same QF1
cloud-bit mask. These input-screening rules therefore should not be attributed
to the Collection 2-to-3 transition based on the code reviewed here.

### Authentication is the largest VIIRS-specific operational change

Collection 2 owns VIIRS login in
[VIIRS_CMR_LOGIN.py](../../ECOv002-L2T-STARS/ECOv002_L2T_STARS/VIIRS/VIIRS_CMR_LOGIN.py).
Its environment fallback is narrower than its docstring suggests: if reading
`.netrc` raises (for example, because the file is absent or malformed), the
outer handler wraps that exception and exits without checking environment
credentials. The environment fallback is reached only when `.netrc` loads but
has no Earthdata entry.
Collection 3 routes login through
[login.py](../../ECOv003-L2T-STARS/ECOv003_L2T_STARS/login.py), shared with
other ECOSTRESS data services. That alters credential precedence and gives
Collection 3 an interactive fallback and a testing bypass. It also moves the
login exception dependency out of the VIIRS-specific module into the shared
exception modules. Deployments should verify which credential source is
selected, especially in unattended jobs where interactive authentication is
not appropriate.

### CMR result handling is slightly more robust, but not a new selection policy

Collection 3 initializes the cached granule DataFrame with explicit columns
and uses bracket-based column access. It changes the diagnostic warning to
trigger only when multiple results are found. The query still sorts granules
by their temporal start and the table still removes duplicates by date and
tile; the diff does not establish a new mission or quality-selection policy.

### `VNP43NRT` wrapper changes are separate from VIIRS decoding

The Collection 3
[VNP43NRT.py](../../ECOv003-L2T-STARS/ECOv003_L2T_STARS/VNP43NRT/VNP43NRT.py)
still uses the Python `VNP09GA` client, but its wrapper has Julia-environment
controls and subprocess setup that Collection 2 lacks. It also changes the
ancillary-download exception name from `AncillaryDownloadFailed` to
`AuxiliaryDownloadFailed`, updates the GEOS5FP import, and routes installation
messages through logging rather than `print`. These are integration changes;
they should not be read as changes to the common `VNP09GA` HDF decoding or
cloud masking.

Collection 2 additionally contains a Julia `VIIRS` module that performs direct
LP DAAC HTTP listing and `wget` downloads. Collection 3's VIIRS paths found in
this comparison are Python-based. If reproducing a Collection 2 run, identify
whether it used this Julia helper or the Python Earthaccess client before
comparing credentials, storage paths, or retrieval behavior.

## Source pointers

- Collection 2 Python VIIRS modules:
  [VIIRS/](../../ECOv002-L2T-STARS/ECOv002_L2T_STARS/VIIRS)
- Collection 3 Python VIIRS modules:
  [VIIRS/](../../ECOv003-L2T-STARS/ECOv003_L2T-STARS/VIIRS)
- Collection 2 NRT wrapper:
  [VNP43NRT.py](../../ECOv002-L2T-STARS/ECOv002_L2T-STARS/VNP43NRT/VNP43NRT.py)
- Collection 3 NRT wrapper:
  [VNP43NRT.py](../../ECOv003-L2T-STARS/ECOv003_L2T-STARS/VNP43NRT/VNP43NRT.py)
- Collection 2 Julia VIIRS helper:
  [VIIRS.jl](../../ECOv002-L2T-STARS/ECOv002_L2T-STARS/STARS_jl/src/VIIRS.jl)

The Collection 2 test tree includes `tests/test_viirs_ecostress.py`; Collection
3 includes `tests/test_vnp43nrt.py` and separate tests for date handling,
configuration, dependencies, and Julia process setup. This report does not
claim that either suite was run or that the outputs are numerically identical.
