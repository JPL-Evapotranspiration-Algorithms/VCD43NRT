# VCD43NRT

VCD43NRT is a Python package for VIIRS/NRT data access and BRDF, albedo, and NDVI processing. The algorithm is exposed through the `VCD43NRT` processor class; VIIRS source-product APIs such as `VNP09GA`, `VJ109GA`, and `VJ209GA` retain their NASA product names.

The data-access layer supports Suomi NPP plus equivalent JPSS VIIRS products for JPSS-1 and JPSS-2 through mission selection and mission-priority fallback.

The package uses Python for VIIRS data access, raster preparation, and output writing, and a Rust BRDF solver exposed through PyO3 for array processing.

The older ECOSTRESS L2T/STARS orchestration code and unrelated product-generation infrastructure have been removed so the package matches the current codebase rather than the earlier full pipeline.

## Purpose

The package provides Python-side support for:

- `VCD43NRT`: near-real-time VIIRS/NRT processing and BRDF correction workflow
- `VNP43IA4`: VIIRS surface reflectance/albedo product interface
- `VNP43MA3`: VIIRS albedo product support

It includes the supporting utilities needed to:

- discover and download VIIRS granules through Earthaccess/CMR
- locate MODLAND tiles and generate grids
- apply solar geometry and BRDF corrections
- manage date ranges and timing helpers
- run the compiled Rust BRDF solver through a Python extension

## Package layout

The active package is organized as a Python package with a Rust extension:

- `VCD43NRT/` — Python package modules
- `VCD43NRT/VCD43NRT.py` — canonical algorithm API
- `rust/` — Rust BRDF solver and PyO3 extension

## Prerequisites

This package needs:

- Python 3.11+
- a compatible published wheel for the current Python version and platform (no Julia or Rust toolchain required)
- the wheel build targets Linux x86_64, macOS x86_64/arm64, and Windows x86_64, using the CPython 3.11 stable ABI
- an EarthData account for downloading data products

## Authentication

The package expects EarthData credentials in `~/.netrc`:

```text
machine urs.earthdata.nasa.gov
login <USERNAME>
password <PASSWORD>
```

## Installation

Install a published release on a supported Python/platform combination:

```bash
pip install VCD43NRT
```

Installing from a source checkout builds the Rust extension and requires a Rust toolchain:

```bash
pip install .
```

The no-toolchain install applies when PyPI has a compatible prebuilt wheel. Unsupported platform/Python combinations and source builds may require Rust.

## Usage

```python
from VCD43NRT import VCD43NRT, VNP09GA
```

The package is designed to be imported as a regular Python module and used from the classes above rather than through the older ECOSTRESS-specific CLI flow.

### Single-granule test run

Use the helper script below to generate one VCD43NRT granule for a specific tile/date:

```bash
python examples/generate_single_VCD43NRT_granule.py \
	--tile h09v05 \
	--date 2026-10-10 \
	--working-directory ./scratch/vcd43nrt
```

Optional flags:

- `--diagnostics` to also write diagnostic layers
- `--mission JPSS1` (or `JPSS2`) to force a specific JPSS mission
- `--fallback-mode --mission-priority SNPP,JPSS1,JPSS2` to automatically try missions in order

### Mission selection and fallback

Default behavior is unchanged and uses Suomi NPP only:

```python
from VCD43NRT import VCD43NRT

processor = VCD43NRT(working_directory="~/data/VCD43NRT")
```

To target one JPSS mission explicitly:

```python
processor = VCD43NRT(
	working_directory="~/data/VCD43NRT",
	mission="JPSS1",
)
```

To enable automatic mission-priority fallback:

```python
processor = VCD43NRT(
	working_directory="~/data/VCD43NRT",
	fallback_mode=True,
	mission_priority=["SNPP", "JPSS1", "JPSS2"],
)
```

If JPSS-1/JPSS-2 concept IDs change or are unavailable by default in your environment,
you can override them with environment variables:

```bash
export VCD43NRT_VJ109GA_CONCEPT_ID="<CMR_CONCEPT_ID>"
export VCD43NRT_VJ209GA_CONCEPT_ID="<CMR_CONCEPT_ID>"
```

## Notes

The former `VNP43NRT` processor import and method remain as deprecated compatibility aliases; new code should use `VCD43NRT`. Existing output directory naming is retained to avoid breaking local caches. This repository supports VIIRS products through Earthaccess/CMR-backed granule discovery and download.

## References

- MODIS/VIIRS BRDF and albedo algorithm literature
- NASA Earthdata and CMR documentation
- VNP43 and VIIRS-related product documentation from the relevant NASA/VIIRS product guides
