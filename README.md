# VCD43NRT

VCD43NRT is a Python package for VIIRS/NRT data access and BRDF, albedo, and NDVI processing. The algorithm is exposed through the `VCD43NRT` processor class; VIIRS source-product APIs such as `VNP09GA`, `VJ109GA`, and `VJ209GA` retain their NASA product names.

The data-access layer supports Suomi NPP plus equivalent JPSS VIIRS products for JPSS-1 and JPSS-2 through mission selection and mission-priority fallback.

The package uses Python for VIIRS data access, raster preparation, and output writing, and a Rust BRDF solver exposed through PyO3 for array processing.

## Contributors 

Gregory H. Halverson (they/them)<br>
[gregory.h.halverson@jpl.nasa.gov](mailto:gregory.h.halverson@jpl.nasa.gov)<br>
NASA Jet Propulsion Laboratory

Margaret C. Johnson (she/her)<br>
[maggie.johnson@jpl.nasa.gov](mailto:maggie.johnson@jpl.nasa.gov)<br>
NASA Jet Propulsion Laboratory

## Purpose

The package provides Python-side support for:

- `VCD43NRT`: near-real-time VIIRS/NRT processing and BRDF correction workflow
- VIIRS source-product APIs for `VNP09GA`, `VJ109GA`, and `VJ209GA`

It includes the supporting utilities needed to:

- discover and download VIIRS granules through Earthaccess/CMR
- locate MODLAND tiles and generate grids
- apply solar geometry and BRDF corrections
- manage date ranges and timing helpers
- run the compiled Rust BRDF solver through a Python extension

## Package layout

The active package is organized as a Python package with a Rust extension:

- `VCD43NRT/` — Python package modules
- `VCD43NRT/VCD43NRT.py` — canonical algorithm implementation and API
- `rust/` — Rust BRDF solver and PyO3 extension

## Prerequisites

This package needs:

- Python 3.11+
- a compatible published wheel for the current Python version and platform (no Julia or Rust toolchain required)
- the wheel build targets Linux x86_64, macOS x86_64/arm64, and Windows x86_64, using the CPython 3.11 stable ABI
- an EarthData account for downloading data products

## Authentication

Earthdata authentication checks `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD`
environment variables first, then credentials in `~/.netrc`, and finally falls
back to interactive login. To use `.netrc`, add:

```text
machine urs.earthdata.nasa.gov
login <USERNAME>
password <PASSWORD>
```

## Installation

The most recent release of the Python/Rust hybrid pip package on PyPi can be installed with pip:

```bash
pip install VCD43NRT
```

## Development

This Python/Rust hybrid package was developed in a mamba environment `VCD43NRT` that can be reproduced using `environment` make target:

```bash
make environment
```

Activate the develop environment using mamba:

```bash
mamba activate VCD43NRT
```

Install this package in development mode using the `install` make target:

```bash
make install
```

## Tests

### Unit Tests

Run the unit tests for this package with the `tests` make target:

```bash
make tests
```

### Smoke Test

Run the smoke test to confirm the Python-Rust binding in this package using the `smoke-test` make target:

```bash
make smoke-test
```

## Usage

```python
from VCD43NRT import VCD43NRT, VNP09GA
```

The package can be imported as a regular Python module or run through the
`VCD43NRT` command-line entry point. The examples below show the Python API and
a single-granule helper script.

The Rust BRDF solver displays progress on stdout when stdout is an interactive
terminal. Indicatif hides the bar when stdout is redirected or captured; the
low-level `nrt_brdf_all` function also accepts `progress=False` to disable it.

### Quick Start

#### Basic Initialization

Initialize the VCD43NRT processor with default settings (Suomi NPP):

```python
from VCD43NRT import VCD43NRT
from datetime import date

# Create processor instance
processor = VCD43NRT(working_directory="~/data/VCD43NRT")

# Process a single granule
granule = processor.VCD43NRT(
    date_UTC=date(2023, 6, 15),
    tile="h11v04"
)

# Access the products
ndvi = granule.NDVI
albedo = granule.albedo
```

#### Command-Line Interface

Process single or multiple granules from the command line:

```bash
# Single date, single tile
python -m VCD43NRT --date 2023-06-15 --tile h11v04

# Date range with multiple tiles (requires scripting)
python -m VCD43NRT --start 2023-06-01 --end 2023-06-30 --tile h11v04

# Enable diagnostic layers (standard error, quality metrics, intermediate data)
python -m VCD43NRT --date 2023-06-15 --tile h11v04 --diagnostics
```

### Processing Workflows

#### Single-Granule Processing

Use the helper script to generate one VCD43NRT granule for a specific tile and date:

```bash
python examples/generate_single_VCD43NRT_granule.py \
	--tile h09v05 \
	--date 2026-10-10 \
	--working-directory ./scratch/vcd43nrt
```

Optional flags:

- `--diagnostics` — Write diagnostic layers (standard errors, R², observation count)
- `--mission JPSS1` or `--mission JPSS2` — Force a specific JPSS mission
- `--fallback-mode --mission-priority SNPP,JPSS1,JPSS2` — Automatically try missions in order

For JPSS-1 specific processing:

```bash
python examples/generate_single_VCD43NRT_granule_JPSS1.py \
	--tile h09v05 \
	--date 2026-10-10 \
	--working-directory ./scratch/vcd43nrt
```

#### Batch Processing Multiple Dates

Process a time series of observations:

```python
from datetime import date, timedelta
from VCD43NRT import VCD43NRT

processor = VCD43NRT(working_directory="~/data/VCD43NRT")

start_date = date(2023, 6, 1)
end_date = date(2023, 6, 30)
tile = "h11v04"

# Process all dates in range
current_date = start_date
while current_date <= end_date:
    try:
        granule = processor.VCD43NRT(date_UTC=current_date, tile=tile)
        print(f"✓ Processed {current_date}")
    except Exception as e:
        print(f"✗ Failed {current_date}: {e}")
    
    current_date += timedelta(days=1)
```

#### Multi-Tile Mosaicking

Create regional mosaics from multiple tiles:

```python
from rasters import RasterGeometry
from VCD43NRT import VCD43NRT

processor = VCD43NRT(working_directory="~/data/VCD43NRT")

# Define target region (example: California)
# Must include spatial reference system
target_geometry = RasterGeometry(...)

# Generate regional albedo mosaic
albedo_mosaic = processor.albedo(
    date_UTC="2023-06-15",
    geometry=target_geometry,
    filename="california_albedo_2023-06-15.tif",
    save_preview=True
)

# Generate regional NDVI mosaic
ndvi_mosaic = processor.NDVI(
    date_UTC="2023-06-15",
    geometry=target_geometry,
    filename="california_ndvi_2023-06-15.tif"
)
```

### Mission Selection and Fallback

#### Default Behavior (Suomi NPP)

```python
from VCD43NRT import VCD43NRT

processor = VCD43NRT(working_directory="~/data/VCD43NRT")
```

#### Explicit Mission Selection (JPSS-1)

```python
processor = VCD43NRT(
	working_directory="~/data/VCD43NRT",
	mission="JPSS1",
)
```

#### Automatic Mission Fallback

Enable automatic fallback to try missions in priority order:

```python
processor = VCD43NRT(
	working_directory="~/data/VCD43NRT",
	fallback_mode=True,
	mission_priority=["SNPP", "JPSS1", "JPSS2"],
)
```

This approach is useful for near-real-time processing when data availability varies by mission.

#### Environment Variable Configuration

Override CMR concept IDs for JPSS missions:

```bash
export VCD43NRT_VJ109GA_CONCEPT_ID="<CMR_CONCEPT_ID>"
export VCD43NRT_VJ209GA_CONCEPT_ID="<CMR_CONCEPT_ID>"
```

### Advanced Usage

#### Accessing BRDF Parameters

Work directly with BRDF parameters before albedo aggregation:

```python
from datetime import date
from VCD43NRT import VCD43NRT

processor = VCD43NRT(working_directory="~/data/VCD43NRT")

# Retrieve raw BRDF parameters
brdf_params = processor.BRDF_parameters(
    date_UTC=date(2023, 6, 15),
    tile="h11v04",
    band="M1"  # VIIRS M-band
)

# Access directional and hemispherical albedos
wsa = brdf_params.WSA      # White-sky albedo (hemispherical)
bsa = brdf_params.BSA      # Black-sky albedo (directional)
nbar = brdf_params.NBAR    # Nadir BRDF-adjusted reflectance

# Quality metrics
r_squared = brdf_params.BRDF_R2     # Model fit quality
std_error = brdf_params.BRDF_SE     # RMS model residual
obs_count = brdf_params.count       # Number of observations used
```

#### Diagnostic Output

Enable diagnostic layers to access uncertainty estimates and intermediate results:

```python
granule = processor.VCD43NRT(
    date_UTC=date(2023, 6, 15),
    tile="h11v04",
    diagnostics=True
)

# Uncertainty quantification
ndvi_se = granule.variable("NBARSE_I1")      # NBAR standard error
albedo_se = granule.variable("WSASE_M1")     # WSA standard error
brdf_se = granule.variable("BRDFSE_M1")      # BRDF model error

# Intermediate layers
aot = granule.variable("AOT")                # Aerosol optical thickness
sza = granule.variable("SZA")                # Solar zenith angle
obs_count = granule.variable("count_M1")     # Observations per pixel

# Individual spectral bands
band1_nbar = granule.NBAR(band=1)            # I-band 1 (red)
band2_nbar = granule.NBAR(band=2)            # I-band 2 (NIR)
```

#### Prefetching Surface Reflectance Data

Pre-download VNP09GA surface reflectance data before BRDF processing:

```python
from datetime import date
from VCD43NRT import VCD43NRT

processor = VCD43NRT(working_directory="~/data/VCD43NRT")

# Pre-download data for a date range
processor.prefetch_VNP09GA(
    start_date=date(2023, 6, 1),
    end_date=date(2023, 6, 30),
    geometry=None  # Download all available
)

# Now process without download delays
granule = processor.VCD43NRT(
    date_UTC=date(2023, 6, 15),
    tile="h11v04"
)
```

#### Working with Granules Directly

Access individual granules for custom processing:

```python
from datetime import date
from VCD43NRT import VCD43NRT

processor = VCD43NRT(working_directory="~/data/VCD43NRT")

# Get granule reference
granule = processor.granule(
    date_UTC=date(2023, 6, 15),
    tile="h11v04"
)

# Access all available variables
available_variables = granule.variables

# Load specific variables
ndvi = granule.get_NDVI()
albedo = granule.get_albedo()
nbar_i1 = granule.NBAR(band=1)
wsa_m1 = granule.WSA(band=1)
bsa_m1 = granule.BSA(band=1)

# Export a specific layer
albedo.to_geotiff("albedo_output.tif")
```

### Data Directory Structure

The default directory structure created by VCD43NRT:

```
~/data/VCD43NRT/
├── VNP09GA_products/          # Surface reflectance data
├── VCD43NRT_products/         # Output BRDF/albedo granules
│   └── VCD43NRT_A<date>_<tile>/
│       ├── NDVI.tif
│       ├── albedo.tif
│       ├── NBAR_I1.tif
│       ├── NBAR_I2.tif
│       ├── WSA_M1.tif
│       ├── BSA_M1.tif
│       └── ...
└── VCD43NRT_staging/          # Temporary intermediate data
```

### Error Handling

Handle common error scenarios:

```python
from datetime import date
from VCD43NRT import VCD43NRT, BRDFRetrievalFailed, AuxiliaryDownloadFailed

processor = VCD43NRT(working_directory="~/data/VCD43NRT")

try:
    granule = processor.VCD43NRT(
        date_UTC=date(2023, 6, 15),
        tile="h11v04"
    )
except BRDFRetrievalFailed as e:
    print(f"Insufficient observations for BRDF retrieval: {e}")
except AuxiliaryDownloadFailed as e:
    print(f"Could not retrieve auxiliary data (e.g., aerosol): {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

## Notes

The former `VNP43NRT` processor class, method, and related directory parameters
remain as deprecated compatibility aliases; new code should use `VCD43NRT` and
the `VCD43NRT` directory parameters. Existing output directory naming is
retained to avoid breaking local caches. This repository supports VIIRS
products through Earthaccess/CMR-backed granule discovery and download.

## References

### BRDF and Albedo Theory

Wanner, W., Li, X., & Strahler, A. H. (1995). On the derivation of kernels for kernel-driven models of bidirectional reflectance. *Journal of Geophysical Research*, 100(D10), 21077-21089. https://doi.org/10.1029/95JD02371

Lewis, P. (1995). The utility of kernel-driven BRDF models in global BRDF and albedo estimation. *Journal of Geophysical Research*, 100(D10), 21097-21112. https://doi.org/10.1029/95JD02372

Roujean, J. L., Leroy, M., & Deschamps, P. Y. (1992). A bidirectional reflectance model of the Earth's surface for the correction of remote sensing data. *Journal of Geophysical Research*, 97(D18), 20455-20468. https://doi.org/10.1029/92JD01411

Strahler, A. H., Muller, J. P., Lucht, W., Schaaf, C., Tsang, T., Lewis, P., & Barnsley, M. J. (1999). MODIS BRDF/Albedo Product: Algorithm Theoretical Basis Document Version 5.0. NASA Goddard Space Flight Center.

### MODIS BRDF and Albedo Products

Schaaf, C. L., Gao, F., Strahler, A. H., Lucht, W., Li, X., Tsang, T., Strugnell, N. C., Zhang, X., Jin, Y., Barker Schaaf, C., ... & Jin, Y. (2002). First operational BRDF, albedo nadir reflectance products from MODIS. *Remote Sensing of Environment*, 83(1-2), 135-148. https://doi.org/10.1016/S0034-4257(02)00084-6

Schaaf, C. B., Liu, J., Gao, F., & Strahler, A. H. (2011). MODIS albedo and reflectance anisotropy products. In *Land Remote Sensing* (pp. 549-561). Springer, New York, NY. https://doi.org/10.1007/978-1-4419-6749-7_24

Liu, J., Schaaf, C., Strahler, A., Zhang, X., Jin, Y., Barker Schaaf, C., Woodcock, C. E., Jain, S., Li, X., Gao, F., ... & He, T. (2018). Validation of Landsat-7/ETM+ land surface reflectance product in North America using Canadian Biosphere Flux Stations. *IEEE Transactions on Geoscience and Remote Sensing*, 56(5), 2842-2857. https://doi.org/10.1109/TGRS.2017.2784479

### VIIRS and NOAA-20/JPSS Data Products

Justice, C. O., Vermote, E., Townshend, J. R., Defries, R., Roy, D. P., Hall, D. K., Salomonson, V. V., Privette, J. L., Riggs, G., Strahler, A., ... & Barnsley, M. J. (2002). The Moderate Resolution Imaging Spectroradiometer (MODIS): Land remote sensing for global change research. *IEEE Transactions on Geoscience and Remote Sensing*, 40(12), 2747-2761. https://doi.org/10.1109/TGRS.2002.805968

Vermote, E., Justice, C. O., & Csiszar, I. (2016). Early evaluation of the VIIRS Level 2 Ocean Color Earth System Data Record. *Remote Sensing of Environment*, 166, 38-49. https://doi.org/10.1016/j.rse.2015.12.007

Jackson, D. L., Wick, G. A., & Harms, E. (2021). NOAA's next generation of Geostationary and polar orbiting satellites for Earth Observation. *Bulletin of the American Meteorological Society*, 102(2), E246-E267.

### Atmospheric Correction and Aerosol Data

Lyapustin, A. I., Wang, Y., Miller, S., & Angal, A. (2014). Multi-angle implementation of atmospheric correction for MODIS (MAIAC): 2. Aerosol algorithm. *IEEE Transactions on Geoscience and Remote Sensing*, 49(12), 4873-4888. https://doi.org/10.1109/TGRS.2011.2144606

Rienecker, M. M., Suarez, M. J., Todling, R., Bacmeister, J., Takacs, L., Liu, H. C., Gu, W., Sienkiewicz, M., Koster, R. D., Gelaro, R., ... & Woollen, J. (2008). The GEOS-5 Data Assimilation System—Documentation of Versions 5.0.1, 5.1.0, and 5.2.0. Technical Report Series on Global Modeling and Data Assimilation, NASA/TM-2008-104606, Vol. 27.

Gelaro, R., McCarty, W., Suárez, M. J., Todling, R., Molod, A., Takacs, L., ... & Zhao, B. (2017). The modern-era retrospective analysis for research and applications, version 2 (MERRA-2). *Journal of Climate*, 30(14), 5419-5454. https://doi.org/10.1175/JCLI-D-16-0758.1

### Vegetation Indices

Rouse, J. W., Haas, R. H., Schell, J. A., & Deering, D. W. (1974). Monitoring vegetation systems in the Great Plains with ERTS. In *Proceedings of the Third Earth Resources Technology Satellite Symposium* (Vol. 1, pp. 48-62). NASA SP-351.

Tucker, C. J. (1979). Red and photographic infrared linear combinations for monitoring vegetation. *Remote Sensing of Environment*, 8(2), 127-150. https://doi.org/10.1016/0034-4257(79)90013-0

Justice, C. O., Townshend, J. R., Holben, B. N., & Curl, C. J. (1990). Analysis of the phenology of global vegetation using meteorological satellite data. *International Journal of Remote Sensing*, 11(7), 1271-1318. https://doi.org/10.1080/01431169008955090

### Solar Geometry and Radiative Transfer

Iqbal, M. (1983). *An Introduction to Solar Radiation*. Academic Press, New York.

Stull, R. (2011). Wet-bulb temperature from relative humidity and air temperature. *Journal of Applied Meteorology and Climatology*, 50(11), 2267-2269. https://doi.org/10.1175/JAMC-D-11-0143.1

Spencer, J. W. (1971). Fourier series representation of the position of the sun. *Search*, 2(5), 172.

### Climate Data Records

Franch, B., Vermote, E., Roger, J. C., Murphy, K., Becker-Reshef, I., Justice, C., Lane, T. P., Jeevalraman, C., Chilukuri, R., Li, H. Y., & Santamaria-Artigas, A. (2014). A 30+ year AVHRR land-surface reflectance climate data record and its application to wheat-yield monitoring. *Remote Sensing of Environment*, 145, 88-102. https://doi.org/10.1016/j.rse.2014.02.010

Povey, A. C., & Grainger, R. G. (2015). Known and unknown unknowns: uncertainty estimation in satellite remote sensing. *Atmospheric Measurement Techniques*, 8(11), 4699-4718. https://doi.org/10.5194/amt-8-4699-2015

### Data Discovery and Access

Earthdata Search - NASA's repository of Earth science data products: https://search.earthdata.nasa.gov/

CMR API Documentation - Common Metadata Repository for Earth Science data discovery: https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html

Earthaccess Python package - Open data access for NASA's Earth science data: https://github.com/nsidc/earthaccess

### Related Tools and Libraries

Rasterio - Geospatial raster data I/O: https://rasterio.readthedocs.io/

GDAL - Geospatial Data Abstraction Library: https://gdal.org/

Xarray - N-dimensional labeled arrays: https://xarray.pydata.org/

NumPy - Numerical computing in Python: https://numpy.org/

## Citation

If you use VCD43NRT in your research, please cite the package using the following BibTeX entry:

```bibtex
@software{halverson2026vcd43nrt,
  title={VCD43NRT: VIIRS/NRT BRDF, Albedo, and NDVI Processing Package},
  author={Halverson, Gregory H. and Johnson, Margaret C.},
  organization={NASA Jet Propulsion Laboratory},
  year={2026},
  url={https://github.com/JPL-Evapotranspiration-Algorithms/VCD43NRT}
}
```
