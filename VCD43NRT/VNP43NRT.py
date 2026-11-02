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

import colored_logging as cl

import rasters
from rasters import Raster, RasterGeometry, Point, Polygon
try:
    from .GEOS5FP import GEOS5FP, FailedGEOS5FPDownload
except ImportError:  # pragma: no cover - legacy direct-import fallback
    from GEOS5FP import GEOS5FP, FailedGEOS5FPDownload
from modland import find_modland_tiles, parsehv, generate_modland_grid

try:
    from ._brdf_rust import nrt_brdf_all
    from .BRDF import bidirectional_reflectance
    from .SZA import calculate_SZA
    from .VIIRSDownloader import VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI
    from .VNP09GA import VNP09GA
    from .VNP09GA_granule import VNP09GAGranule
    from .VIIRS_granule import ALBEDO_COLORMAP, NDVI_COLORMAP
    from .VIIRS import VIIRSUnavailableError
    from .daterange import date_range
    from .timer import Timer
except ImportError:  # pragma: no cover - legacy direct-import fallback
    from _brdf_rust import nrt_brdf_all
    from BRDF import bidirectional_reflectance
    from SZA import calculate_SZA
    from VIIRSDownloader import VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI
    from VNP09GA import VNP09GA
    from VNP09GA_granule import VNP09GAGranule
    from VIIRS_granule import ALBEDO_COLORMAP, NDVI_COLORMAP
    from VIIRS import VIIRSUnavailableError
    from daterange import date_range
    from timer import Timer

DEFAULT_WEIGHTED = True
DEFAULT_SCALE = 1.87

with open(join(abspath(dirname(__file__)), "version.txt")) as f:
    version = f.read()

logger = logging.getLogger(__name__)

class BRDFRetrievalFailed(RuntimeError):
    pass

class BRDFParameters:
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
    def __init__(self, directory: str):
        self._directory = abspath(directory)

    def __repr__(self):
        return f"VCD43NRTGranule({self.directory})"

    @property
    def directory(self):
        return self._directory

    @property
    def granule_ID(self) -> str:
        return splitext(basename(self.directory))[0]

    @property
    def tile(self):
        return self.granule_ID.split("_")[2]

    @property
    def hv(self):
        return parsehv(self.tile)

    @property
    def h(self):
        return self.hv[0]

    @property
    def v(self):
        return self.hv[1]

    @property
    def date_UTC(self):
        return datetime.strptime(self.granule_ID.split("_")[1][1:], "%Y%j")

    @property
    def variables(self) -> List[str]:
        return [
            splitext(basename(filename))[0].split("_")[-1]
            for filename
            in glob(join(self.directory, "*.tif"))
        ]

    def variable_filename(self, variable_name: str) -> str:
        return join(self.directory, f"{self.granule_ID}_{variable_name}.tif")

    def variable(self, variable_name: str, geometry: RasterGeometry = None, cmap: Colormap = None) -> Raster:
        return Raster.open(self.variable_filename(variable_name), geometry=geometry, cmap=cmap)

    def get_NDVI(self, geometry: RasterGeometry = None, cmap: Colormap = NDVI_COLORMAP) -> Raster:
        return self.variable("NDVI", geometry=geometry, cmap=cmap)

    NDVI = property(get_NDVI)

    def get_albedo(self, geometry: RasterGeometry = None, cmap: Colormap = ALBEDO_COLORMAP) -> Raster:
        return self.variable("albedo", geometry=geometry, cmap=cmap)

    albedo = property(get_albedo)

    def BSA(self, band: int, geometry: RasterGeometry = None) -> Raster:
        return self.variable(f"BSA_M{band}", geometry=geometry)

    def WSA(self, band: int, geometry: RasterGeometry = None) -> Raster:
        return self.variable(f"WSA_M{band}", geometry=geometry)

    def NBAR(self, band: int, geometry: RasterGeometry = None) -> Raster:
        return self.variable(f"NBAR_I{band}", geometry=geometry)

    def add_layer(self, variable_name: str, image: Raster) -> str:
        if not isinstance(image, Raster):
            raise TypeError(f"VCD43NRT layer '{variable_name}' must be a rasters.Raster, got {type(image).__name__}")

        filename = self.variable_filename(variable_name)
        logger.info(f"adding VCD43NRT layer at {cl.place(self.tile)} on {cl.time(self.date_UTC)}: {cl.file(filename)}")
        image.to_COG(filename)

        return filename

    @property
    def complete(self) -> bool:
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
    pass


class VCD43NRT(VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI):
    DEFAULT_VNP09GA_DIRECTORY = "VNP09GA_products"
    DEFAULT_VCD43NRT_DIRECTORY = "VNP43NRT_products"
    DEFAULT_VNP43NRT_DIRECTORY = DEFAULT_VCD43NRT_DIRECTORY

    def __init__(
            self,
            working_directory: str = None,
            download_directory: str = None,
            VNP09GA_directory: str = None,
            VNP43NRT_directory: str = None,
            mosaic_directory: str = None,
            VNP43NRT_staging_directory: str = None,
            GEOS5FP_connection: GEOS5FP = None,
            GEOS5FP_download: str = None,
            mission: str = "SNPP",
            mission_priority: Optional[Sequence[str]] = None,
            fallback_mode: bool = False,
            VCD43NRT_directory: str = None,
            VCD43NRT_staging_directory: str = None):
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
            VCD43NRT_staging_directory = join(working_directory, "VNP43NRT_staging")

        if VNP09GA_directory is None:
            VNP09GA_directory = join(working_directory, self.DEFAULT_VNP09GA_DIRECTORY)

        VNP09GA_directory = abspath(expanduser(VNP09GA_directory))

        if VCD43NRT_directory is None:
            VCD43NRT_directory = VNP43NRT_directory
        if VCD43NRT_directory is None:
            VCD43NRT_directory = join(working_directory, self.DEFAULT_VCD43NRT_DIRECTORY)

        VCD43NRT_directory = abspath(expanduser(VCD43NRT_directory))
        VCD43NRT_staging_directory = abspath(expanduser(VCD43NRT_staging_directory))

        self.vnp09ga = VNP09GA(
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
        return self.vnp09ga.granule(
            date_UTC=date_UTC,
            tile=tile,
        )

    def prefetch_VNP09GA(
            self,
            start_date: Union[date, str],
            end_date: Union[date, str],
            geometry: Point or Polygon or RasterGeometry = None):
        self.vnp09ga.prefetch_VNP09GA(
            start_date,
            end_date,
            geometry,
        )

    def generate_staging_directory(self, tile: str, variable: str) -> str:
        return join(self.VCD43NRT_staging_directory, tile, variable)

    def generate_staging_filename(self, tile: str, processing_date, variable: str) -> str:
        return join(self.generate_staging_directory(tile, variable), f"{processing_date:%Y-%m-%d}_{variable}.tif")

    def BRDF_parameters(
            self,
            date_UTC: Union[date, str],
            tile: str,
            band: str):
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

        for processing_date in date_range(start_date, end_date):
            logger.info(f"retrieving VNP09GA for VCD43NRT at {cl.place(tile)} on {cl.time(date_UTC)}")

            try:
                granule = self.VNP09GA(processing_date, tile)

                reflectance_filename = self.generate_staging_filename(tile, processing_date, band)

                if exists(reflectance_filename):
                    logger.info(f"previously generated {band} reflectance on {processing_date}: {reflectance_filename}")
                else:
                    logger.info(f"generating {band} reflectance on {processing_date}")
                    reflectance_raster = granule.band(band)
                    logger.info(f"writing {band} reflectance on {processing_date}: {reflectance_filename}")
                    reflectance_raster.to_geotiff(reflectance_filename)

                solar_zenith_filename = self.generate_staging_filename(tile, processing_date, f"{band_type}_solar_zenith")

                if exists(solar_zenith_filename):
                    logger.info(f"previously generated solar zenith on {processing_date}: {solar_zenith_filename}")
                else:
                    logger.info(f"generating solar zenith on {processing_date}")
                    solar_zenith_raster = granule.solar_zenith(band)
                    logger.info(f"writing solar zenith on {processing_date}: {solar_zenith_filename}")
                    solar_zenith_raster.to_geotiff(solar_zenith_filename)

                sensor_zenith_filename = self.generate_staging_filename(tile, processing_date, f"{band_type}_sensor_zenith")

                if exists(sensor_zenith_filename):
                    logger.info(f"previously generated sensor zenith on {processing_date}: {sensor_zenith_filename}")
                else:
                    logger.info(f"generating sensor zenith on {processing_date}")
                    sensor_zenith_raster = granule.sensor_zenith(band)
                    logger.info(f"writing sensor zenith on {processing_date}: {sensor_zenith_filename}")
                    sensor_zenith_raster.to_geotiff(sensor_zenith_filename)

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

        SZA_filename = self.generate_staging_filename(tile, date_UTC, f"{band_type}_solar_zenith_noon")

        if exists(SZA_filename):
            logger.info(f"solar zenith noon file already exists: {SZA_filename}")
        else:
            doy = date_UTC.timetuple().tm_yday
            SZA = calculate_SZA(doy, 12, grid)
            logger.info(f"writing solar zenith noon: {SZA_filename}")
            SZA.to_geotiff(SZA_filename)

        logger.info(f"started processing VCD43NRT BRDF parameters at {cl.place(tile)} on {cl.time(date_UTC)}")
        timer = Timer()
        if not observation_dates:
            raise BRDFRetrievalFailed(f"no reflectance/angle observations available for {tile} on {date_UTC}")

        shape = geometry.shape
        reflectance_directory = self.generate_staging_directory(tile, band)
        solar_zenith_directory = self.generate_staging_directory(tile, f"{band_type}_solar_zenith")
        sensor_zenith_directory = self.generate_staging_directory(tile, f"{band_type}_sensor_zenith")
        relative_azimuth_directory = self.generate_staging_directory(tile, f"{band_type}_relative_azimuth")
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
        results = nrt_brdf_all(
            Y,
            sz,
            vz,
            rz,
            solar_noon,
            weighted=DEFAULT_WEIGHTED,
            scale=DEFAULT_SCALE,
        )
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
            f"finished processing VCD43NRT BRDF parameters at {cl.place(tile)} on {cl.time(date_UTC)} ({cl.time(timer)})")

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
        if isinstance(date_UTC, str):
            date_UTC = parser.parse(date_UTC).date()

        granule_ID = f"VNP43NRT_A{date_UTC:%Y%j}_{tile}"

        if self.fallback_mode:
            granule_ID = f"{granule_ID}_JPSS"
        elif self.mission != "SNPP":
            granule_ID = f"{granule_ID}_{self.mission}"

        return granule_ID

    def granule_directory(
            self,
            date_UTC: Union[date, str],
            tile: str) -> str:
        return join(self.VCD43NRT_directory, self.granule_ID(date_UTC, tile))

    def AOT(self, time_UTC: datetime, geometry: RasterGeometry = None, resampling: str = None) -> Raster:
        try:
            return self.GEOS5FP.AOT(time_UTC=time_UTC, geometry=geometry, resampling=resampling)
        except FailedGEOS5FPDownload as e:
            raise AuxiliaryDownloadFailed("unable to retrieve AOT from GEOS5-FP for VCD43NRT") from e

    def VCD43NRT(
            self,
            date_UTC: Union[date, str],
            tile: str,
            diagnostics: bool = False) -> VCD43NRTGranule:
        if isinstance(date_UTC, str):
            date_UTC = parser.parse(date_UTC).date()

        logger.info(f"started processing VCD43NRT at {cl.place(tile)} on {cl.time(date_UTC)}")
        timer = Timer()

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

        NIR = granule.variable("NBAR_I2")
        red = granule.variable("NBAR_I1")
        NDVI = Raster(rasters.clip((NIR - red) / (NIR + red), -1, 1), geometry=NIR.geometry)
        granule.add_layer("NDVI", NDVI)

        time_UTC = datetime(date_UTC.year, date_UTC.month, date_UTC.day, 10, 30)
        geometry = generate_modland_grid(*parsehv(tile), 1200)
        AOT = self.AOT(time_UTC=time_UTC, geometry=geometry, resampling="cubic")

        if diagnostics:
            granule.add_layer("AOT", AOT)

        doy = date_UTC.timetuple().tm_yday
        SZA = calculate_SZA(doy, 10.5, geometry)

        if diagnostics:
            granule.add_layer("SZA", SZA)

        b = {}

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
        logger.info(f"finished processing VCD43NRT at {cl.place(tile)} on {cl.time(date_UTC)} ({cl.time(timer)})")

        return granule

    def VNP43NRT(
            self,
            date_UTC: Union[date, str],
            tile: str,
            diagnostics: bool = False) -> VCD43NRTGranule:
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
    def __init__(self, *args, **kwargs):
        warnings.warn(
            "VNP43NRT is deprecated; use VCD43NRT instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)


def main(argv=sys.argv):
    cl.configure()

    parser = argparse.ArgumentParser(description="run the VCD43NRT BRDF/NDVI/albedo algorithm")

    parser.add_argument(
        "--date",
        dest="date"
    )

    parser.add_argument(
        "--start",
        dest="start"
    )

    parser.add_argument(
        "--end",
        dest="end"
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

    if args.start is not None:
        start = args.start
    elif args.start is None and args.date is not None:
        start = args.date
    else:
        raise ValueError("no dates given")

    end = args.end

    if end is None:
        end = start

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

    for date_UTC in date_range(start, end):
        granule = vcd43nrt.VCD43NRT(
            date_UTC=date_UTC,
            tile=tile,
            diagnostics=diagnostics
        )


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv))
