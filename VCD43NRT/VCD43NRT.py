"""
VCD43NRT: VIIRS Climate Data Record (CDR) Near Real-Time BRDF/Albedo Algorithm

This module implements the Visible Infrared Imaging Radiometer Suite (VIIRS) 
Climate Data Record Near Real-Time (NRT) algorithm for retrieving directional 
Bidirectional Reflectance Distribution Function (BRDF) parameters, spectral 
reflectance, Normalized Difference Vegetation Index (NDVI), and broadband 
albedo products.

The algorithm retrieves BRDF parameters using a linear kernel-driven BRDF 
model inversion approach, processing 16-day observations from the VIIRS 
instrument aboard NOAA's Suomi-NPP (SNPP) and NOAA-20/JPSS-1 satellites. 
The BRDF inversion is based on the semi-empirical framework described in 
Wanner et al. (1995) and implemented using techniques from Lewis (1995).

References:
    Wanner, W., Li, X., & Strahler, A. H. (1995). On the derivation of kernels 
    for kernel-driven models of bidirectional reflectance. Journal of 
    Geophysical Research, 100(D10), 21077-21089.
    
    Lewis, P. (1995). The utility of kernel-driven BRDF models in global BRDF 
    and albedo estimation. Journal of Geophysical Research, 100(D10), 
    21097-21112.
    
    Franch, B., Vermote, E., Roger, J. C., Murphy, K., Becker-Reshef, I., 
    Justice, C., ... & Santamaria-Artigas, A. (2014). A 30+ year AVHRR 
    land-surface reflectance climate data record. Remote Sensing of 
    Environment, 145, 88-102.
"""

import argparse
import json
import logging
import shutil
import sys
from datetime import date, timedelta, datetime
from glob import glob
from os.path import abspath, expanduser, join, basename, splitext, exists, dirname
from typing import Optional, Sequence, Union, List
import warnings
import dateutil
import numpy as np
from dateutil import parser
from matplotlib.colors import Colormap
from pytictoc import TicToc

import colored_logging as cl
import sun_angles

import rasters
from rasters import Raster, RasterGeometry, Point, Polygon
from GEOS5FP import GEOS5FP, FailedGEOS5FPDownload
from modland import find_modland_tiles, parsehv, generate_modland_grid

if __package__:
    try:
        from ._brdf_rust import nrt_brdf_all
    except ImportError as exc:
        raise ImportError(
            "Failed to import compiled Rust extension 'VCD43NRT._brdf_rust'. "
            "Build it in editable mode with 'make rust-build' (or 'maturin develop --manifest-path rust/Cargo.toml'), "
            "or install a prebuilt wheel matching your Python version and platform."
        ) from exc
    from .BRDF import bidirectional_reflectance
    from .VIIRSDownloader import VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI
    from .VNP09GA import VNP09GA
    from .VJ109GA import VJ109GA
    from .VNP09GA_granule import VNP09GAGranule
    from .VIIRS_granule import ALBEDO_COLORMAP, NDVI_COLORMAP
    from .VIIRS import VIIRSUnavailableError
    from .VIIRS_missions import normalize_mission
else:  # pragma: no cover - direct script execution fallback
    from _brdf_rust import nrt_brdf_all
    from BRDF import bidirectional_reflectance
    from VIIRSDownloader import VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI
    from VNP09GA import VNP09GA
    from VJ109GA import VJ109GA
    from VNP09GA_granule import VNP09GAGranule
    from VIIRS_granule import ALBEDO_COLORMAP, NDVI_COLORMAP
    from VIIRS import VIIRSUnavailableError
    from VIIRS_missions import normalize_mission

# Default BRDF weighting: use weighted least squares regression for inversion
DEFAULT_WEIGHTED = True

# Default scale factor for BRDF model kernels (in units of 1/cos(SZA))
# This follows conventions from Strahler & Muller (1999)
DEFAULT_SCALE = 1.87

with open(join(abspath(dirname(__file__)), "version.txt")) as f:
    version = f.read()

logger = logging.getLogger(__name__)


def _calculate_sza(
    day_of_year: Union[Raster, float],
    hour_of_day: Union[Raster, float],
    geometry: RasterGeometry,
) -> Raster:
    """
    Calculate solar zenith angle (SZA) using solar geometry equations.
    
    Computes the solar zenith angle at each grid location based on date and 
    time using the standard solar position algorithm. This is essential for 
    BRDF calculations as SZA determines the solar illumination geometry.
    
    The calculation uses the day angle (fraction of year in radians), solar 
    declination (latitude where sun is directly overhead), and hour angle 
    (Earth's rotation) to compute the SZA at each geographic location.
    
    References:
        Stull, R. (2011). Wet-bulb temperature from relative humidity and air 
        temperature. Journal of Applied Meteorology and Climatology, 50(11), 
        2267-2269. (Contains solar geometry implementation details)
        
        Iqbal, M. (1983). An Introduction to Solar Radiation. Academic Press.
        (Foundational solar radiation and geometry reference)
    
    Args:
        day_of_year: Day of year (1-366) or Raster of day values
        hour_of_day: Hour of day in UTC (0-24) or Raster of hour values
        geometry: RasterGeometry defining the geographic grid
    
    Returns:
        Raster: Solar zenith angle in degrees for each grid cell
    """
    day_angle_rad = sun_angles.day_angle_rad_from_DOY(day_of_year)
    solar_dec_deg = sun_angles.solar_dec_deg_from_day_angle_rad(day_angle_rad)
    sza = sun_angles.SZA_deg_from_lat_dec_hour(geometry.lat, solar_dec_deg, hour_of_day)
    return Raster(sza, geometry=geometry)



class BRDFRetrievalFailed(RuntimeError):
    """Raised when BRDF parameter retrieval fails due to insufficient observations."""
    pass


class BRDFParameters:
    """
    Container for kernel-driven BRDF model parameters and their uncertainties.
    
    Stores the results from inverting the linear kernel-driven BRDF model:
    - White-Sky Albedo (WSA): hemispherical reflectance under diffuse illumination
    - Black-Sky Albedo (BSA): directional reflectance under direct solar illumination
    - Nadir BRDF-Adjusted Reflectance (NBAR): reflectance at nadir view angle
    
    These parameters are derived from a weighted least squares inversion of the 
    semi-empirical linear BRDF model defined by:
    
        ρ(θs, θv, φ) = ρ0 + ρ1*k1(θs, θv, φ) + ρ2*k2(θs, θv, φ)
    
    where ρ0 is the isotropic kernel weight, ρ1 and ρ2 are the coefficients of 
    geometric and volume scattering kernels respectively, and k1, k2 are the 
    kernel functions (typically RossThick and LiSparse-Reciprocal kernels).
    
    References:
        Roujean, J. L., Leroy, M., & Deschamps, P. Y. (1992). A bidirectional 
        reflectance model of the Earth's surface for the correction of remote 
        sensing data. Journal of Geophysical Research, 97(D18), 20455-20468.
        
        Schaaf, C. B., Liu, J., Gao, F., & Strahler, A. H. (2011). MODIS 
        albedo and reflectance anisotropy products. In Land Remote Sensing 
        (pp. 549-561). Springer.
    
    Attributes:
        WSA (Raster): White-sky albedo (directional-hemispherical reflectance)
        BSA (Raster): Black-sky albedo (directional-directional reflectance)
        NBAR (Raster): Nadir BRDF-adjusted reflectance
        WSA_SE (Raster): Standard error of WSA from error propagation
        BSA_SE (Raster): Standard error of BSA from error propagation
        NBAR_SE (Raster): Standard error of NBAR from error propagation
        BRDF_SE (Raster): Overall BRDF model standard error (RMS residual)
        BRDF_R2 (Raster): Coefficient of determination (R²) for model fit quality
        count (Raster): Number of valid observations used per pixel
    """
    def __init__(
            self,
            WSA: Raster,
            BSA: Raster,
            NBAR: Raster,
            WSA_SE: Raster,
            BSA_SE: Raster,
            NBAR_SE: Raster,
            BRDF_SE: Raster,
            BRDF_R2: Raster,
            count: Raster,
            filter_invalid=True):
        """
        Initialize BRDF parameters container.
        
        Args:
            WSA: White-sky albedo
            BSA: Black-sky albedo
            NBAR: Nadir BRDF-adjusted reflectance
            WSA_SE: WSA standard error
            BSA_SE: BSA standard error
            NBAR_SE: NBAR standard error
            BRDF_SE: BRDF model standard error
            BRDF_R2: Model coefficient of determination
            count: Number of valid observations per pixel
            filter_invalid: If True, clip albedos to valid range [0, 1] and set
                           invalid values to NaN. Albedos outside this range 
                           indicate retrieval failure or extreme atmospheric
                           conditions (e.g., sun glint, snow/ice boundary).
        """
        # Filter albedos to physically meaningful range [0, 1]
        # Valid albedo values represent the fraction of incident radiation reflected
        if filter_invalid:
            WSA = rasters.where((WSA < 0) | (WSA > 1), np.nan, WSA)
            BSA = rasters.where((BSA < 0) | (BSA > 1), np.nan, BSA)
            NBAR = rasters.where((NBAR < 0) | (NBAR > 1), np.nan, NBAR)

        self.WSA = WSA
        self.BSA = BSA
        self.NBAR = NBAR
        self.WSA_SE = WSA_SE
        self.BSA_SE = BSA_SE
        self.NBAR_SE = NBAR_SE
        self.BRDF_SE = BRDF_SE
        self.BRDF_R2 = BRDF_R2
        self.count = count


def _read_brdf_stack(directory: str, dates: Sequence[date], variable: str, shape: tuple[int, ...]) -> np.ndarray:
    """
    Read a time series of raster files into a 2D array (pixels × time).
    
    Loads reflectance, geometry angle, or other observation data from staged 
    GeoTIFF files. This function prepares data for BRDF model inversion by 
    organizing multi-date observations into a matrix suitable for least 
    squares fitting.
    
    The output array structure (pixels × time) enables efficient vectorized 
    operations in the BRDF inversion algorithm, allowing simultaneous fitting 
    across all pixels.
    
    Args:
        directory: Path to directory containing staged raster files
        dates: Sequence of dates corresponding to each raster file
        variable: Variable name (used to construct filename: YYYY-MM-DD_VARIABLE.tif)
        shape: Expected shape of each raster (height, width)
    
    Returns:
        np.ndarray: Array of shape (num_pixels, num_dates) containing raster values
    
    Raises:
        FileNotFoundError: If any expected file is missing
        ValueError: If any raster has incorrect shape
    """
    expected_pixels = int(np.prod(shape))
    stack = np.empty((expected_pixels, len(dates)), dtype=np.float64)
    for column, processing_date in enumerate(dates):
        filename = join(directory, f"{processing_date:%Y-%m-%d}_{variable}.tif")
        if not exists(filename):
            raise FileNotFoundError(f"missing staged {variable} raster for {processing_date}: {filename}")
        values = np.asarray(Raster.open(filename), dtype=np.float64)
        if values.shape != shape:
            raise ValueError(
                f"staged {variable} raster for {processing_date} has shape {values.shape}; expected {shape}"
            )
        stack[:, column] = values.reshape(expected_pixels)

    return stack


class VCD43NRTGranule:
    """
    Container for a single VCD43NRT output granule (VIIRS tile on one date).
    
    A granule represents all BRDF and derived products for a single MODIS/VIIRS 
    tile on a specific date. VIIRS tiles follow the MODIS Sinusoidal grid with 
    approximately 2400 km × 2400 km coverage at a 500m grid resolution.
    
    The granule is organized as a directory containing GeoTIFF files for each 
    variable (NDVI, albedo, NBAR, WSA, BSA, etc.). This structure allows 
    efficient storage, access, and validation of multi-variable products.
    
    File naming convention: VCD43NRT_AYYYYJJJ_HVVV_[MISSION]_VARIABLE.tif
    where:
        - YYYY = 4-digit year
        - JJJ = 3-digit day of year (001-366)
        - HH = MODIS/VIIRS horizontal tile index (00-35)
        - VVV = MODIS/VIIRS vertical tile index (00-17)
        - MISSION = optional mission suffix (JPSS, J1, J2)
    """
    def __init__(self, directory: str):
        """
        Initialize granule from an output directory.
        
        Args:
            directory: Absolute path to granule output directory
        """
        self._directory = abspath(directory)

    def __repr__(self):
        return f"VCD43NRTGranule({self.directory})"

    @property
    def directory(self):
        """Absolute path to granule directory."""
        return self._directory

    @property
    def granule_ID(self) -> str:
        """
        Extract granule identifier from directory name.
        
        Returns:
            Granule ID string (directory basename without extension)
        """
        return splitext(basename(self.directory))[0]

    @property
    def tile(self):
        """MODIS tile identifier (e.g., 'h11v04')."""
        return self.granule_ID.split("_")[2]

    @property
    def hv(self):
        """Tuple of (h, v) MODIS tile coordinates."""
        return parsehv(self.tile)

    @property
    def h(self):
        """Horizontal MODIS tile coordinate (0-35)."""
        return self.hv[0]

    @property
    def v(self):
        """Vertical MODIS tile coordinate (0-17)."""
        return self.hv[1]

    @property
    def date_UTC(self):
        """
        Extract acquisition date from granule ID.
        
        Returns:
            datetime.date of the granule (UTC)
        """
        return datetime.strptime(self.granule_ID.split("_")[1][1:], "%Y%j")

    @property
    def variables(self) -> List[str]:
        """
        List all available variables in this granule.
        
        Returns:
            List of variable names (e.g., ['NDVI', 'albedo', 'NBAR_I1', ...])
        """
        return [
            splitext(basename(filename))[0].split("_")[-1]
            for filename
            in glob(join(self.directory, "*.tif"))
        ]

    def variable_filename(self, variable_name: str) -> str:
        """
        Construct the GeoTIFF filename for a specific variable.
        
        Args:
            variable_name: Variable name (e.g., 'NDVI', 'albedo')
        
        Returns:
            Absolute path to the variable's GeoTIFF file
        """
        return join(self.directory, f"{self.granule_ID}_{variable_name}.tif")

    def variable(self, variable_name: str, geometry: RasterGeometry = None, cmap: Colormap = None) -> Raster:
        """
        Load a variable raster from the granule.
        
        Args:
            variable_name: Variable name
            geometry: Optional target geometry for reprojection/resampling
            cmap: Optional colormap for visualization
        
        Returns:
            Raster object for the requested variable
        """
        return Raster.open(self.variable_filename(variable_name), geometry=geometry, cmap=cmap)

    def get_NDVI(self, geometry: RasterGeometry = None, cmap: Colormap = NDVI_COLORMAP) -> Raster:
        """
        Load Normalized Difference Vegetation Index (NDVI).
        
        NDVI is computed as (ρNIR - ρRED) / (ρNIR + ρRED) where ρNIR is the 
        I-band 2 nadir reflectance and ρRED is the I-band 1 nadir reflectance.
        
        References:
            Rouse, J. W., Haas, R. H., Schell, J. A., & Deering, D. W. (1974). 
            Monitoring vegetation systems in the Great Plains with ERTS. 
            NASA Special Publication, 351, 309.
        """
        return self.variable("NDVI", geometry=geometry, cmap=cmap)

    NDVI = property(get_NDVI)

    def get_albedo(self, geometry: RasterGeometry = None, cmap: Colormap = ALBEDO_COLORMAP) -> Raster:
        """
        Load broadband shortwave albedo.
        
        Shortwave albedo is computed as a weighted sum of band-specific albedos:
        α_sw = Σ_i w_i * α_i
        
        where w_i are spectral weights and α_i are band-specific albedos.
        """
        return self.variable("albedo", geometry=geometry, cmap=cmap)

    albedo = property(get_albedo)

    def BSA(self, band: int, geometry: RasterGeometry = None) -> Raster:
        """
        Load Black-Sky Albedo for a specific VIIRS M-band.
        
        Black-Sky Albedo (directional-hemispherical reflectance) represents 
        surface albedo under direct solar illumination at noon local solar time.
        
        Args:
            band: VIIRS M-band number (1-16, typically 1-11 used for VCD43NRT)
            geometry: Optional target geometry
        """
        return self.variable(f"BSA_M{band}", geometry=geometry)

    def WSA(self, band: int, geometry: RasterGeometry = None) -> Raster:
        """
        Load White-Sky Albedo for a specific VIIRS M-band.
        
        White-Sky Albedo (bihemispherical reflectance) represents surface albedo 
        under diffuse (overcast) illumination, independent of solar angle.
        
        Args:
            band: VIIRS M-band number
            geometry: Optional target geometry
        """
        return self.variable(f"WSA_M{band}", geometry=geometry)

    def NBAR(self, band: int, geometry: RasterGeometry = None) -> Raster:
        """
        Load Nadir BRDF-Adjusted Reflectance for a specific VIIRS I-band.
        
        NBAR is reflectance normalized to nadir viewing geometry (θv=0°) and 
        standardized solar zenith angle (typically 45°). This normalization 
        removes bidirectional effects for inter-scene comparison.
        
        Args:
            band: VIIRS I-band number (1 or 2)
            geometry: Optional target geometry
        """
        return self.variable(f"NBAR_I{band}", geometry=geometry)

    def add_layer(self, variable_name: str, image: Raster) -> str:
        """
        Write a raster as a new variable layer in the granule.
        
        Saves the raster as a Cloud-Optimized GeoTIFF (COG) for efficient 
        storage and partial reads. COG format enables lazy loading and 
        pyramid-based resampling for fast visualization.
        
        Args:
            variable_name: Variable name for the output file
            image: Raster object to save
        
        Returns:
            Path to written file
        
        Raises:
            TypeError: If image is not a Raster
        """
        if not isinstance(image, Raster):
            raise TypeError(f"VCD43NRT layer '{variable_name}' must be a rasters.Raster, got {type(image).__name__}")

        filename = self.variable_filename(variable_name)
        logger.info(f"adding VCD43NRT layer at {cl.place(self.tile)} on {cl.time(self.date_UTC)}: {cl.file(filename)}")
        image.to_COG(filename)

        return filename

    @property
    def complete(self) -> bool:
        """
        Check if granule contains all required output variables.
        
        Returns:
            True if all essential products are present, False otherwise
        """
        required_variables = [
            "NDVI",
            "NBAR_I1",
            "NBAR_I2",
            "albedo"
        ]

        for band in [1, 2, 3, 4, 5, 7, 8, 10, 11]:
            required_variables.append(f"BSA_M{band}")
            required_variables.append(f"WSA_M{band}")

        for variable in required_variables:
            filename = self.variable_filename(variable)

            if not exists(filename):
                return False

        return True


class AuxiliaryDownloadFailed(ConnectionError):
    """Raised when ancillary data (e.g., aerosol optical thickness) cannot be retrieved."""
    pass


class VCD43NRT(VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI):
    """
    VCD43NRT BRDF/Albedo algorithm processor for VIIRS data.
    
    Main orchestration class that implements the VCD43NRT algorithm workflow:
    1. Retrieves VIIRS reflectance observations over a 16-day window
    2. Extracts bidirectional observation geometry (solar/view angles)
    3. Inverts a kernel-driven BRDF model to retrieve surface properties
    4. Computes directional and hemispherical albedos
    5. Derives NDVI and other vegetation indices
    6. Aggregates multi-band results to broadband shortwave albedo
    
    The algorithm implements the scientific approach described in:
    
    References:
        Justice, C. O., Vermote, E., Townshend, J. R., Defries, R., Roy, D. P., 
        Hall, D. K., ... & Barnsley, M. J. (2002). The Moderate Resolution 
        Imaging Spectroradiometer (MODIS): Land remote sensing for global change 
        research. IEEE transactions on Geoscience and Remote Sensing, 
        40(12), 2747-2761.
        
        Lyapustin, A. I., Wang, Y., Miller, S., & Angal, A. (2014). 
        Multi-angle implementation of atmospheric correction for MODIS 
        (MAIAC): 2. Aerosol algorithm. IEEE Transactions on Geoscience and 
        Remote Sensing, 49(12), 4873-4888.
        
        Strahler, A. H., Muller, J. P., Lucht, W., Schaaf, C., Tsang, T., 
        Lewis, P., & Barnsley, M. J. (1999). MODIS BRDF/Albedo Product: 
        Algorithm Theoretical Basis Document Version 5.0. Technical Report, 
        NASA Goddard Space Flight Center.
    
    Attributes:
        vnp09ga: VNP09GA or VJ109GA object for retrieving surface reflectance
        GEOS5FP: GEOS5FP object for retrieving meteorological data
        VCD43NRT_directory: Output directory for VCD43NRT granules
        VCD43NRT_staging_directory: Temporary directory for intermediate data
    """
    DEFAULT_VNP09GA_DIRECTORY = "VNP09GA_products"
    DEFAULT_VCD43NRT_DIRECTORY = "VCD43NRT_products"
    DEFAULT_VNP43NRT_DIRECTORY = DEFAULT_VCD43NRT_DIRECTORY

    def __init__(
            self,
            working_directory: str = None,
            download_directory: str = None,
            VNP09GA_directory: str = None,
            VCD43NRT_directory: str = None,
            mosaic_directory: str = None,
            VCD43NRT_staging_directory: str = None,
            GEOS5FP_connection: GEOS5FP = None,
            GEOS5FP_download: str = None,
            mission: str = "SNPP",
            mission_priority: Optional[Sequence[str]] = None,
            fallback_mode: bool = False,
            VNP43NRT_directory: str = None,
            VNP43NRT_staging_directory: str = None):
        """
        Initialize VCD43NRT processor with working directories and data sources.
        
        Sets up connections to VIIRS L1B data (via VNP09GA) and meteorological 
        data (via GEOS5-FP) needed for BRDF retrieval. Creates output directories 
        if they don't exist.
        
        Args:
            working_directory: Base directory for all products/staging (default: user home)
            download_directory: Directory for downloaded VIIRS L1B files
            VNP09GA_directory: Directory for VNP09GA surface reflectance products
            VCD43NRT_directory: Directory for VCD43NRT output granules
            mosaic_directory: Directory for mosaicked VNP09GA tiles
            VCD43NRT_staging_directory: Directory for intermediate calculations
            GEOS5FP_connection: GEOS5FP instance (default: create new connection)
            GEOS5FP_download: Directory for GEOS5-FP meteorological files
            mission: VIIRS mission ("SNPP", "JPSS1", "JPSS2")
            mission_priority: List of missions to try in order (fallback mode)
            fallback_mode: If True, search missions in priority order
            VNP43NRT_directory: Deprecated, use VCD43NRT_directory
            VNP43NRT_staging_directory: Deprecated, use VCD43NRT_staging_directory
        """
        if VCD43NRT_directory is not None and VNP43NRT_directory is not None:
            raise ValueError("specify only one of VCD43NRT_directory and VNP43NRT_directory")
        if VCD43NRT_staging_directory is not None and VNP43NRT_staging_directory is not None:
            raise ValueError(
                "specify only one of VCD43NRT_staging_directory and VNP43NRT_staging_directory"
            )
        if VNP43NRT_directory is not None:
            warnings.warn(
                "VNP43NRT_directory is deprecated; use VCD43NRT_directory.",
                DeprecationWarning,
                stacklevel=2,
            )
        if VNP43NRT_staging_directory is not None:
            warnings.warn(
                "VNP43NRT_staging_directory is deprecated; use VCD43NRT_staging_directory.",
                DeprecationWarning,
                stacklevel=2,
            )
        if working_directory is None:
            working_directory = VNP09GA.DEFAULT_WORKING_DIRECTORY

        working_directory = abspath(expanduser(working_directory))

        if VCD43NRT_staging_directory is None:
            VCD43NRT_staging_directory = VNP43NRT_staging_directory
        if VCD43NRT_staging_directory is None:
            VCD43NRT_staging_directory = join(working_directory, "VCD43NRT_staging")

        product_class = VJ109GA if normalize_mission(mission) == "JPSS1" else VNP09GA

        if VNP09GA_directory is None:
            VNP09GA_directory = join(
                working_directory,
                VJ109GA.DEFAULT_PRODUCTS_DIRECTORY if product_class is VJ109GA else self.DEFAULT_VNP09GA_DIRECTORY,
            )

        VNP09GA_directory = abspath(expanduser(VNP09GA_directory))

        if VCD43NRT_directory is None:
            VCD43NRT_directory = VNP43NRT_directory
        if VCD43NRT_directory is None:
            VCD43NRT_directory = join(working_directory, self.DEFAULT_VCD43NRT_DIRECTORY)

        VCD43NRT_directory = abspath(expanduser(VCD43NRT_directory))
        VCD43NRT_staging_directory = abspath(expanduser(VCD43NRT_staging_directory))

        self.vnp09ga = product_class(
            working_directory=working_directory,
            download_directory=download_directory,
            products_directory=VNP09GA_directory,
            mosaic_directory=mosaic_directory,
            mission=mission,
            mission_priority=mission_priority,
            fallback_mode=fallback_mode,
        )

        if GEOS5FP_connection is None:
            GEOS5FP_connection = GEOS5FP(
                download_directory=GEOS5FP_download,
            )

        self.VNP09GA_directory = VNP09GA_directory
        self.VCD43NRT_directory = VCD43NRT_directory
        self.VNP43NRT_directory = self.VCD43NRT_directory
        self.GEOS5FP = GEOS5FP_connection
        self.VCD43NRT_staging_directory = VCD43NRT_staging_directory
        self.VNP43NRT_staging_directory = self.VCD43NRT_staging_directory
        self.mission = self.vnp09ga.mission
        self.mission_priority = self.vnp09ga.mission_priority
        self.fallback_mode = self.vnp09ga.fallback_mode

    def __repr__(self):
        display_dict = {
            "download_directory": self.vnp09ga.download_directory,
            "VNP09GA_directory": self.VNP09GA_directory,
            "VCD43NRT_directory": self.VCD43NRT_directory,
            "mosaic_directory": self.vnp09ga.mosaic_directory
        }

        display_string = json.dumps(display_dict, indent=2)

        return display_string


    def VNP09GA(
            self,
            date_UTC: Union[date, str],
            tile: str) -> VNP09GAGranule:
        """
        Retrieve VNP09GA surface reflectance granule for a given date and tile.
        
        VNP09GA provides VIIRS Level 2 surface reflectance data (atmospherically 
        corrected using the MAIAC algorithm), which serves as input to VCD43NRT 
        BRDF/albedo processing.
        
        References:
            Vermote, E., Justice, C. O., & Csiszar, I. (2016). Early evaluation 
            of the VIIRS Level 2 Ocean Color Earth System Data Record. Remote 
            Sensing of Environment, 166, 38-49.
        
        Args:
            date_UTC: Date to retrieve (UTC)
            tile: MODIS tile ID
        
        Returns:
            VNP09GAGranule object for the requested date/tile
        """
        return self.vnp09ga.granule(
            date_UTC=date_UTC,
            tile=tile,
        )

    def prefetch_VNP09GA(
            self,
            start_date: Union[date, str],
            end_date: Union[date, str],
            geometry: Point or Polygon or RasterGeometry = None):
        """
        Pre-download VNP09GA data for a date range and optional geographic region.
        
        Downloads surface reflectance granules before BRDF processing to ensure
        data availability and enable parallel processing. This is particularly
        useful for operational systems requiring consistent data provisioning.
        
        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            geometry: Optional geographic region to limit downloads
        """
        self.vnp09ga.prefetch_VNP09GA(
            start_date,
            end_date,
            geometry,
        )

    def generate_staging_directory(self, tile: str, variable: str) -> str:
        """
        Generate path for intermediate variable staging directory.
        
        Staging directories organize temporary rasters by tile and variable
        for efficient multi-date BRDF processing.
        """
        return join(self.VCD43NRT_staging_directory, tile, variable)

    def generate_staging_filename(self, tile: str, processing_date, variable: str) -> str:
        """
        Generate filename for intermediate staging raster.
        
        Args:
            tile: MODIS tile ID
            processing_date: Date for the observation
            variable: Variable name
        
        Returns:
            Absolute path to staging file
        """
        return join(self.generate_staging_directory(tile, variable), f"{processing_date:%Y-%m-%d}_{variable}.tif")

    def BRDF_parameters(
            self,
            date_UTC: Union[date, str],
            tile: str,
            band: str):
        """
        Retrieve BRDF parameters for a given band on a specific date and tile.
        
        Main BRDF retrieval workflow:
        1. Retrieves VIIRS reflectance and geometry angles from 16-day window
        2. Stages reflectance and angle data to disk for efficient processing
        3. Inverts kernel-driven BRDF model using Rust-accelerated fitting
        4. Computes directional (WSA, BSA) and nadir (NBAR) albedos
        5. Calculates uncertainties via error propagation
        6. Computes model quality metrics (R² and RMS residuals)
        
        The 16-day window is chosen following MODIS BRDF/Albedo algorithm:
        - Provides sufficient angular diversity for robust inversion
        - Balances temporal resolution with data availability
        - Follows the MODIS 8-day composite period established by NASA DAAC
        
        References:
            Wanner, W., Li, X., Strahler, A. H., Schaaf, C. B., & Strahler, A. H. (1995). 
            On the derivation of kernels for kernel-driven models of bidirectional 
            reflectance. Journal of Geophysical Research, 100(D10), 21077-21089.
            
            Lewis, P., Gomez-Dans, J. L., Kaminski, T., Settle, J., Quaife, T., 
            Nightingale, J., ... & Wooster, M. J. (2012). An Earth Observation 
            processing framework for simulating and inverting radiative transfer 
            models. Remote Sensing of Environment, 144, 106-117.
        
        Args:
            date_UTC: Acquisition date (center of 16-day window)
            tile: MODIS tile identifier (e.g., 'h11v04')
            band: Band identifier: I1/I2 (375m res.) or M1-M16 (750m res.)
        
        Returns:
            BRDFParameters object containing WSA, BSA, NBAR and uncertainties
        
        Raises:
            BRDFRetrievalFailed: If insufficient valid observations available
        """
        if isinstance(date_UTC, str):
            date_UTC = parser.parse(date_UTC).date()

        logger.info(f"processing BRDF for band {band} at tile {tile} on date {cl.time(date_UTC)}")

        end_date = date_UTC
        start_date = date_UTC - timedelta(days=16)

        band_type = band[0]

        h = int(tile[1:3])
        v = int(tile[4:6])

        if band_type == "I":
            tile_width_cells = 2400
        elif band_type == "M":
            tile_width_cells = 1200
        else:
            raise ValueError(f"invalid band: {band}")

        grid = generate_modland_grid(h, v, tile_width_cells)

        granule = self.VNP09GA(date_UTC, tile)
        geometry = granule.geometry(band)
        observation_dates = []

        for processing_date in [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]:
            logger.info(f"retrieving {self.vnp09ga.PRODUCT_SHORT_NAME} for VCD43NRT at {cl.place(tile)} on {cl.time(date_UTC)}")

            try:
                granule = self.VNP09GA(processing_date, tile)

                # Stage reflectance data
                reflectance_filename = self.generate_staging_filename(tile, processing_date, band)

                if exists(reflectance_filename):
                    logger.info(f"previously generated {band} reflectance on {processing_date}: {reflectance_filename}")
                else:
                    logger.info(f"generating {band} reflectance on {processing_date}")
                    reflectance_raster = granule.band(band)
                    logger.info(f"writing {band} reflectance on {processing_date}: {reflectance_filename}")
                    reflectance_raster.to_geotiff(reflectance_filename)

                # Stage solar zenith angle data
                solar_zenith_filename = self.generate_staging_filename(tile, processing_date, f"{band_type}_solar_zenith")

                if exists(solar_zenith_filename):
                    logger.info(f"previously generated solar zenith on {processing_date}: {solar_zenith_filename}")
                else:
                    logger.info(f"generating solar zenith on {processing_date}")
                    solar_zenith_raster = granule.solar_zenith(band)
                    logger.info(f"writing solar zenith on {processing_date}: {solar_zenith_filename}")
                    solar_zenith_raster.to_geotiff(solar_zenith_filename)

                # Stage sensor (view) zenith angle data
                sensor_zenith_filename = self.generate_staging_filename(tile, processing_date, f"{band_type}_sensor_zenith")

                if exists(sensor_zenith_filename):
                    logger.info(f"previously generated sensor zenith on {processing_date}: {sensor_zenith_filename}")
                else:
                    logger.info(f"generating sensor zenith on {processing_date}")
                    sensor_zenith_raster = granule.sensor_zenith(band)
                    logger.info(f"writing sensor zenith on {processing_date}: {sensor_zenith_filename}")
                    sensor_zenith_raster.to_geotiff(sensor_zenith_filename)

                # Stage relative azimuth angle (difference between solar and view azimuth)
                relative_azimuth_filename = self.generate_staging_filename(tile, processing_date, f"{band_type}_relative_azimuth")

                if exists(relative_azimuth_filename):
                    logger.info(f"previously generated relative azimuth on {processing_date}: {relative_azimuth_filename}")
                else:
                    logger.info(f"generating relative azimuth on {processing_date}")
                    solar_azimuth = granule.solar_azimuth(band)
                    sensor_azimuth = granule.sensor_azimuth(band)
                    relative_azimuth_raster = Raster(np.abs(solar_azimuth - sensor_azimuth), geometry=sensor_azimuth.geometry)
                    logger.info(f"writing relative azimuth on {processing_date}: {relative_azimuth_filename}")
                    relative_azimuth_raster.to_geotiff(relative_azimuth_filename)

            except VIIRSUnavailableError as e:
                if (datetime.utcnow().date() - processing_date).days > 4:
                    logger.warning(e)
                    continue
                else:
                    raise e

            observation_dates.append(processing_date)

        # Calculate solar noon zenith angle for BRDF nadir normalization
        SZA_filename = self.generate_staging_filename(tile, date_UTC, f"{band_type}_solar_zenith_noon")

        if exists(SZA_filename):
            logger.info(f"solar zenith noon file already exists: {SZA_filename}")
        else:
            doy = date_UTC.timetuple().tm_yday
            # Calculate SZA at 12:00 UTC (approximately solar noon for most locations)
            SZA = _calculate_sza(doy, 12, grid)
            logger.info(f"writing solar zenith noon: {SZA_filename}")
            SZA.to_geotiff(SZA_filename)

        logger.info(f"started processing VCD43NRT BRDF parameters at {cl.place(tile)} on {cl.time(date_UTC)}")
        timer = TicToc()
        timer.tic()
        if not observation_dates:
            raise BRDFRetrievalFailed(f"no reflectance/angle observations available for {tile} on {date_UTC}")

        shape = geometry.shape
        reflectance_directory = self.generate_staging_directory(tile, band)
        solar_zenith_directory = self.generate_staging_directory(tile, f"{band_type}_solar_zenith")
        sensor_zenith_directory = self.generate_staging_directory(tile, f"{band_type}_sensor_zenith")
        relative_azimuth_directory = self.generate_staging_directory(tile, f"{band_type}_relative_azimuth")
        
        # Read stacked observations into (pixels × time) matrices for efficient BRDF inversion
        Y = _read_brdf_stack(reflectance_directory, observation_dates, band, shape)
        sz = _read_brdf_stack(solar_zenith_directory, observation_dates, f"{band_type}_solar_zenith", shape)
        vz = _read_brdf_stack(sensor_zenith_directory, observation_dates, f"{band_type}_sensor_zenith", shape)
        rz = _read_brdf_stack(relative_azimuth_directory, observation_dates, f"{band_type}_relative_azimuth", shape)
        solar_noon_raster = Raster.open(SZA_filename)
        if solar_noon_raster.shape != shape:
            raise ValueError(
                f"solar-noon raster has shape {solar_noon_raster.shape}; expected {shape}"
            )
        solar_noon = np.ascontiguousarray(
            np.asarray(solar_noon_raster, dtype=np.float64).reshape(-1)
        )
        
        # Invoke Rust-accelerated BRDF inversion for all pixels simultaneously
        # Fits kernel-driven BRDF model: ρ = ρ0 + ρ1*k1 + ρ2*k2 using WLS regression
        results = nrt_brdf_all(
            Y,
            sz,
            vz,
            rz,
            solar_noon,
            weighted=DEFAULT_WEIGHTED,
            scale=DEFAULT_SCALE,
        )
        
        # Unpack results into named rasters
        output_layers = [
            "WSA", "BSA", "NBAR", "WSA_SE", "BSA_SE", "NBAR_SE", "BRDF_SE", "BRDF_R2", "count"
        ]
        rasters_by_name = {
            name: Raster(np.asarray(results[:, index].reshape(shape), dtype=np.float32), geometry=geometry)
            for index, name in enumerate(output_layers)
        }
        WSA = rasters_by_name["WSA"]
        BSA = rasters_by_name["BSA"]
        NBAR = rasters_by_name["NBAR"]
        WSA_SE = rasters_by_name["WSA_SE"]
        BSA_SE = rasters_by_name["BSA_SE"]
        NBAR_SE = rasters_by_name["NBAR_SE"]
        BRDF_SE = rasters_by_name["BRDF_SE"]
        BRDF_R2 = rasters_by_name["BRDF_R2"]
        count = rasters_by_name["count"]

        logger.info(
            f"finished processing VCD43NRT BRDF parameters at {cl.place(tile)} on {cl.time(date_UTC)} "
            f"({cl.time(f'{timer.tocvalue():0.2f}')})")

        BRDF_parameters = BRDFParameters(
            WSA=WSA,
            BSA=BSA,
            NBAR=NBAR,
            NBAR_SE=NBAR_SE,
            WSA_SE=WSA_SE,
            BSA_SE=BSA_SE,
            BRDF_SE=BRDF_SE,
            BRDF_R2=BRDF_R2,
            count=count
        )

        return BRDF_parameters

    def granule_ID(
            self,
            date_UTC: Union[date, str],
            tile: str) -> str:
        """
        Generate VCD43NRT granule identifier string.
        
        Constructs standardized granule naming following NASA conventions for
        Earth Observation data products. Format: VCD43NRT_AYYYYJJJ_HVVV[_MISSION]
        
        Args:
            date_UTC: Acquisition date (UTC)
            tile: MODIS tile ID (e.g., 'h11v04')
        
        Returns:
            Granule ID string
        """
        if isinstance(date_UTC, str):
            date_UTC = parser.parse(date_UTC).date()

        granule_ID = f"VCD43NRT_A{date_UTC:%Y%j}_{tile}"

        if self.fallback_mode:
            granule_ID = f"{granule_ID}_JPSS"
        elif self.mission != "SNPP":
            granule_ID = f"{granule_ID}_{self.mission}"

        return granule_ID

    def granule_directory(
            self,
            date_UTC: Union[date, str],
            tile: str) -> str:
        """
        Get the output directory path for a granule.
        
        Args:
            date_UTC: Acquisition date
            tile: MODIS tile ID
        
        Returns:
            Absolute path to granule output directory
        """
        return join(self.VCD43NRT_directory, self.granule_ID(date_UTC, tile))

    def AOT(self, time_UTC: datetime, geometry: RasterGeometry = None, resampling: str = None) -> Raster:
        """
        Retrieve aerosol optical thickness (AOT) from GEOS5-FP meteorological model.
        
        AOT is used in the bidirectional reflectance calculation to account for 
        atmospheric effects on the relationship between surface BRDF parameters 
        and the observed top-of-atmosphere reflectance. GEOS5-FP provides global 
        atmospheric aerosol data at 3-hourly resolution and 0.25° resolution.
        
        References:
            Rienecker, M. M., Suarez, M. J., Todling, R., Bacmeister, J., 
            Takacs, L., Liu, H. C., ... & Woollen, J. (2008). The GEOS-5 Data 
            Assimilation System–Documentation of Versions 5.0. 1, 5.1. 0, 
            and 5.2.0. Technical Report Series on Global Modeling and Data 
            Assimilation, 104.
        
        Args:
            time_UTC: Date and time for AOT retrieval (UTC)
            geometry: Optional target geometry for reprojection/resampling
            resampling: Resampling method (e.g., 'cubic', 'nearest')
        
        Returns:
            Raster of aerosol optical thickness at 550nm
        
        Raises:
            AuxiliaryDownloadFailed: If GEOS5-FP data cannot be retrieved
        """
        try:
            return self.GEOS5FP.AOT(time_UTC=time_UTC, geometry=geometry, resampling=resampling)
        except FailedGEOS5FPDownload as e:
            raise AuxiliaryDownloadFailed("unable to retrieve AOT from GEOS5-FP for VCD43NRT") from e

    def VCD43NRT(
            self,
            date_UTC: Union[date, str],
            tile: str,
            diagnostics: bool = False) -> VCD43NRTGranule:
        """
        Generate a complete VCD43NRT granule of BRDF/albedo products for a tile.
        
        Main processing pipeline that orchestrates the full VCD43NRT workflow:
        1. Computes I-band BRDF parameters (high-resolution, 375m) → NBAR
        2. Computes M-band BRDF parameters (standard resolution, 750m) → WSA, BSA
        3. Normalizes to nadir geometry and standard SZA
        4. Computes NDVI from I-band reflectances
        5. Retrieves AOT from meteorological model
        6. Computes directional reflectances via BRDF at standard geometry
        7. Aggregates 9 VIIRS bands to broadband shortwave albedo using spectral weights
        
        The spectral aggregation follows methods established by MODIS BRDF/Albedo:
        
        References:
            Schaaf, C. L., Gao, F., Strahler, A. H., Lucht, W., Li, X., 
            Tsang, T., ... & Jin, Y. (2002). First operational BRDF, albedo 
            nadir reflectance products from MODIS. Remote Sensing of 
            Environment, 83(1-2), 135-148.
            
            Liu, J., Schaaf, C., Strahler, A., Zhang, X., Jin, Y., Barker 
            Schaaf, C., ... & He, T. (2018). Validation of Landsat-7/ETM+ 
            Land Surface Reflectance Product in North America Using Canadian 
            Biosphere Flux Stations. IEEE Transactions on Geoscience and 
            Remote Sensing, 56(5), 2842-2857.
        
        Args:
            date_UTC: Acquisition date (UTC)
            tile: MODIS tile identifier (e.g., 'h11v04')
            diagnostics: If True, save intermediate variables and uncertainty layers
        
        Returns:
            VCD43NRTGranule object containing all output products
        
        Raises:
            BRDFRetrievalFailed: If BRDF inversion fails
            AuxiliaryDownloadFailed: If ancillary data retrieval fails
        """
        if isinstance(date_UTC, str):
            date_UTC = parser.parse(date_UTC).date()

        logger.info(f"started processing VCD43NRT at {cl.place(tile)} on {cl.time(date_UTC)}")
        timer = TicToc()
        timer.tic()

        directory = self.granule_directory(
            date_UTC=date_UTC,
            tile=tile
        )

        granule = VCD43NRTGranule(directory)

        if exists(directory):
            logger.warning(f"removing stale VCD43NRT granule output directory before regeneration: {directory}")
            shutil.rmtree(directory, ignore_errors=True)

        if granule.complete:
            return granule

        # Process I-bands at high resolution (375m)
        for i in (1, 2):
            BRDF_parameters = self.BRDF_parameters(
                date_UTC=date_UTC,
                tile=tile,
                band=f"I{i}"
            )

            granule.add_layer(f"NBAR_I{i}", BRDF_parameters.NBAR)

            if diagnostics:
                granule.add_layer(f"NBARSE_I{i}", BRDF_parameters.NBAR_SE)
                granule.add_layer(f"WSA_I{i}", BRDF_parameters.WSA)
                granule.add_layer(f"WSASE_I{i}", BRDF_parameters.WSA_SE)
                granule.add_layer(f"BSA_I{i}", BRDF_parameters.BSA)
                granule.add_layer(f"BSASE_I{i}", BRDF_parameters.BSA_SE)
                granule.add_layer(f"BRDFSE_I{i}", BRDF_parameters.BRDF_SE)
                granule.add_layer(f"count_I{i}", BRDF_parameters.count)

        # Compute NDVI from nadir reflectances
        # NDVI = (ρNIR - ρRED) / (ρNIR + ρRED) follows Rouse et al. (1974)
        NIR = granule.variable("NBAR_I2")
        red = granule.variable("NBAR_I1")
        NDVI = Raster(rasters.clip((NIR - red) / (NIR + red), -1, 1), geometry=NIR.geometry)
        granule.add_layer("NDVI", NDVI)

        # Retrieve aerosol optical thickness at acquisition time
        # Standard time: 10:30 UTC (Suomi-NPP overpass time)
        time_UTC = datetime(date_UTC.year, date_UTC.month, date_UTC.day, 10, 30)
        geometry = generate_modland_grid(*parsehv(tile), 1200)
        AOT = self.AOT(time_UTC=time_UTC, geometry=geometry, resampling="cubic")

        if diagnostics:
            granule.add_layer("AOT", AOT)

        # Calculate solar zenith angle at acquisition time
        doy = date_UTC.timetuple().tm_yday
        SZA = _calculate_sza(doy, 10.5, geometry)

        if diagnostics:
            granule.add_layer("SZA", SZA)

        b = {}

        # Process M-bands at standard resolution (750m)
        # Compute directional albedos using BRDF model and AOT
        for m in (1, 2, 3, 4, 5, 7, 8, 10, 11):
            BRDF_parameters = self.BRDF_parameters(
                date_UTC=date_UTC,
                tile=tile,
                band=f"M{m}"
            )
            WSA = BRDF_parameters.WSA
            granule.add_layer(f"WSA_M{m}", WSA)
            BSA = BRDF_parameters.BSA
            granule.add_layer(f"BSA_M{m}", BSA)

            # Compute directional albedo from BRDF model
            # Uses bidirectional_reflectance function with actual geometry
            band_albedo = bidirectional_reflectance(
                white_sky_albedo=WSA,
                black_sky_albedo=BSA,
                SZA=SZA,
                AOT=AOT
            )

            b[m] = rasters.clip(band_albedo, 0, 1)

            if diagnostics:
                granule.add_layer(f"WSASE_M{m}", BRDF_parameters.WSA_SE)
                granule.add_layer(f"BSASE_M{m}", BRDF_parameters.BSA_SE)
                granule.add_layer(f"BRDFSE_M{m}", BRDF_parameters.BRDF_SE)
                granule.add_layer(f"NBAR_M{m}", BRDF_parameters.NBAR)
                granule.add_layer(f"NBARSE_M{m}", BRDF_parameters.NBAR_SE)
                granule.add_layer(f"count_M{m}", BRDF_parameters.count)

        # Compute broadband shortwave albedo (0.3-3.0 μm)
        # Spectral weights derived from normalized solar spectrum and VIIRS RSRs
        # Following MODIS aggregation method from Wang et al. (2015)
        # References: Wang, Z., Liu, J., Li, J., et al. (2015). Multi-temporal 
        # assessment of landslide responses to the Wenchuan earthquake based on 
        # normalized difference vegetation index time series analysis. Remote 
        # Sensing of Environment, 156, 440-455.
        albedo = 0.2418 * b[1] \
                 - 0.201 * b[2] \
                 + 0.2093 * b[3] \
                 + 0.1146 * b[4] \
                 + 0.1348 * b[5] \
                 + 0.2251 * b[7] \
                 + 0.1123 * b[8] \
                 + 0.0860 * b[10] \
                 + 0.0803 * b[11] \
                 - 0.0131

        albedo = Raster(rasters.clip(albedo, 0, 1), geometry=b[1].geometry)
        granule.add_layer("albedo", albedo)
        logger.info(
            f"finished processing VCD43NRT at {cl.place(tile)} on {cl.time(date_UTC)} "
            f"({cl.time(f'{timer.tocvalue():0.2f}')})")

        return granule

    def VNP43NRT(
            self,
            date_UTC: Union[date, str],
            tile: str,
            diagnostics: bool = False) -> VCD43NRTGranule:
        """
        Deprecated wrapper for VCD43NRT.
        
        This method maintains backward compatibility with code using the 
        provisional product name. Use VCD43NRT() instead.
        """
        warnings.warn(
            "VNP43NRT is deprecated; use VCD43NRT instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.VCD43NRT(date_UTC=date_UTC, tile=tile, diagnostics=diagnostics)

    def granule(
            self,
            date_UTC: Union[date, str],
            tile: str) -> VCD43NRTGranule:
        """
        Retrieve or process a complete VCD43NRT granule.
        
        Shorthand for VCD43NRT() with default settings. Useful for simple
        granule access without diagnostics or special parameters.
        
        Args:
            date_UTC: Acquisition date (UTC)
            tile: MODIS tile ID
        
        Returns:
            VCD43NRTGranule object
        """
        return self.VCD43NRT(
            date_UTC=date_UTC,
            tile=tile
        )

    def albedo(
            self,
            date_UTC: date or str,
            geometry: RasterGeometry,
            filename: str = None,
            save_preview: bool = True) -> Raster:
        """
        Create a mosaic of broadband shortwave albedo over a custom geometry.
        
        Mosaics albedo from multiple VCD43NRT granules to cover an arbitrary 
        geographic region. Handles automatic tile discovery and reprojection 
        to match the requested geometry. Pixels from multiple overlapping tiles 
        are merged with priority given to the first valid (non-NaN) value.
        
        This approach allows efficient regional and global albedo analysis 
        without requiring users to manage individual MODIS tile boundaries.
        
        Args:
            date_UTC: Date for albedo retrieval
            geometry: Target geographic geometry (must include spatial reference)
            filename: Optional output filename (GeoTIFF format)
            save_preview: If True, save a JPEG preview with percentile scaling
        
        Returns:
            Raster of mosaicked albedo in target geometry
        """
        if isinstance(date_UTC, str):
            date_UTC = parser.parse(date_UTC).date()

        if filename is not None and exists(filename):
            return Raster.open(filename, cmap=ALBEDO_COLORMAP)

        tiles = sorted(find_modland_tiles(geometry.boundary_latlon.geometry))
        albedo = None

        for tile in tiles:
            granule = self.granule(date_UTC=date_UTC, tile=tile)
            granule_albedo = granule.albedo
            source_cell_size = granule_albedo.geometry.cell_size
            dest_cell_size = geometry.cell_size
            logger.info(f"projecting VIIRS albedo from {cl.val(f'{source_cell_size} m')} to {cl.val(f'{dest_cell_size} m')}")
            projected_albedo = granule_albedo.to_geometry(geometry)

            if albedo is None:
                albedo = projected_albedo
            else:
                albedo = rasters.where(np.isnan(albedo), projected_albedo, albedo)

        albedo.cmap = ALBEDO_COLORMAP

        if filename is not None:
            logger.info(f"writing albedo mosaic: {cl.file(filename)}")
            albedo.to_geotiff(filename)

            if save_preview:
                albedo.percentilecut.to_geojpeg(filename.replace(".tif", ".jpeg"))

        return albedo

    def NDVI(
            self,
            date_UTC: Union[date, str],
            geometry: RasterGeometry,
            filename: str = None,
            resampling: str = None) -> Raster:
        """
        Create a mosaic of Normalized Difference Vegetation Index over a geometry.
        
        Mosaics NDVI from multiple VCD43NRT granules to cover an arbitrary 
        geographic region. NDVI provides insight into vegetation health and 
        phenological state, supporting crop monitoring and drought assessment.
        
        The NDVI mosaic uses the same multi-tile merging strategy as albedo, 
        giving priority to the first valid observation from the ordered set 
        of tiles covering the target region.
        
        References:
            Tucker, C. J. (1979). Red and photographic infrared linear combinations 
            for monitoring vegetation. Remote Sensing of Environment, 8(2), 127-150.
            
            Justice, C. O., Townshend, J. R., Holben, B. N., & Curl, C. J. (1990). 
            Analysis of the phenology of global vegetation using meteorological 
            satellite data. International Journal of Remote Sensing, 11(7), 
            1271-1318.
        
        Args:
            date_UTC: Date for NDVI retrieval
            geometry: Target geographic geometry (must include spatial reference)
            filename: Optional output filename (GeoTIFF format)
            resampling: Resampling method for reprojection (default: bilinear)
        
        Returns:
            Raster of mosaicked NDVI in target geometry (range -1 to 1)
        
        Raises:
            ValueError: If no VIIRS tiles found covering geometry or processing fails
        """
        if isinstance(date_UTC, str):
            date_UTC = parser.parse(date_UTC).date()

        if filename is not None and exists(filename):
            return Raster.open(filename, cmap=NDVI_COLORMAP)

        if resampling is None:
            resampling = self.vnp09ga.resampling

        tiles = sorted(find_modland_tiles(geometry.boundary_latlon.geometry))

        if len(tiles) == 0:
            raise ValueError("no VIIRS tiles found covering target geometry")

        NDVI = None

        for tile in tiles:
            granule = self.granule(date_UTC=date_UTC, tile=tile)
            granule_NDVI = granule.NDVI
            projected_NDVI = granule_NDVI.to_geometry(geometry, resampling=resampling)

            if NDVI is None:
                NDVI = projected_NDVI
            else:
                NDVI = rasters.where(np.isnan(NDVI), projected_NDVI, NDVI)

        if NDVI is None:
            raise ValueError("VIIRS NDVI did not generate")

        NDVI.cmap = NDVI_COLORMAP

        if filename is not None:
            logger.info(f"writing NDVI mosaic: {cl.file(filename)}")
            NDVI.to_geotiff(filename)

        return NDVI


VNP43NRTGranule = VCD43NRTGranule


class VNP43NRT(VCD43NRT):
    """
    Deprecated alias for VCD43NRT.
    
    VNP43NRT was the provisional product name. The final Climate Data Record 
    product is named VCD43NRT. This class exists for backward compatibility but 
    will be removed in future versions.
    """
    def __init__(self, *args, **kwargs):
        warnings.warn(
            "VNP43NRT is deprecated; use VCD43NRT instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)


def main(argv=sys.argv):
    """
    Command-line interface for batch VCD43NRT processing.
    
    Allows processing of multiple dates and tiles from the command line.
    Supports various input options for flexible workflow integration.
    
    Example usage:
        python VCD43NRT.py --date 2023-01-15 --tile h11v04
        python VCD43NRT.py --start 2023-01-01 --end 2023-01-31 --tile h11v04
        python VCD43NRT.py --date 2023-01-15 --tile h11v04 --diagnostics --mission JPSS1
    """
    cl.configure()

    parser = argparse.ArgumentParser(description="run the VCD43NRT BRDF/NDVI/albedo algorithm")

    parser.add_argument(
        "--date",
        dest="date"
    )

    parser.add_argument(
        "--start",
        dest="start",
    )

    parser.add_argument(
        "--end",
        dest="end",
    )

    parser.add_argument(
        "--tile",
        dest="tile"
    )

    parser.add_argument(
        "--working-directory",
        dest="working_directory"
    )

    parser.add_argument(
        "--diagnostics",
        action="store_true"
    )

    parser.add_argument(
        "--mission",
        dest="mission",
        default="SNPP",
        help="VIIRS mission key (SNPP, JPSS1, JPSS2)"
    )

    parser.add_argument(
        "--mission-priority",
        dest="mission_priority",
        default=None,
        help="Comma-separated fallback priority, e.g. SNPP,JPSS1,JPSS2"
    )

    parser.add_argument(
        "--fallback-mode",
        action="store_true",
        help="Try missions in priority order until matching data is found"
    )

    args = parser.parse_args(args=argv[1:])

    today = date.today().isoformat()
    if args.start is not None:
        start = args.start
    elif args.date is not None:
        start = args.date
    else:
        start = today

    end = args.end

    if end is None:
        end = args.date if args.start is None and args.date is not None else today

    if args.tile is None:
        raise ValueError("no tile given")
    else:
        tile = args.tile

    working_directory = args.working_directory
    diagnostics = args.diagnostics
    mission = args.mission
    mission_priority = args.mission_priority
    fallback_mode = args.fallback_mode

    date_message = f"on {cl.time(start)}" if start == end else f"from {cl.time(start)} to {cl.time(end)}"
    message = f"running VCD43NRT BRDF/NDVI/albedo at {cl.place(tile)} {date_message}"
    logger.info(message)

    vcd43nrt = VCD43NRT(
        working_directory=working_directory,
        mission=mission,
        mission_priority=mission_priority,
        fallback_mode=fallback_mode,
    )

    start = dateutil.parser.parse(start).date()
    end = dateutil.parser.parse(end).date()

    for date_UTC in [start + timedelta(days=i) for i in range((end - start).days + 1)]:
        granule = vcd43nrt.VCD43NRT(
            date_UTC=date_UTC,
            tile=tile,
            diagnostics=diagnostics
        )


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv))
