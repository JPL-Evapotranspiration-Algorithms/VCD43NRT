import importlib
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

import VCD43NRT as pkg
import VCD43NRT.exceptions as exc_mod
viirs_mod = importlib.import_module("VCD43NRT.VIIRS")
proc_mod = importlib.import_module("VCD43NRT.VCD43NRT")


def test_exception_hierarchy():
    assert issubclass(exc_mod.VCD43NRTError, Exception)
    assert issubclass(exc_mod.CMRServerUnreachable, exc_mod.VCD43NRTError)
    assert issubclass(exc_mod.DownloadFailed, exc_mod.VCD43NRTError)
    assert issubclass(exc_mod.DownloadFailed, ConnectionError)
    assert issubclass(exc_mod.AuxiliaryDownloadFailed, exc_mod.DownloadFailed)
    assert issubclass(exc_mod.VIIRSUnavailableError, exc_mod.VCD43NRTError)
    assert issubclass(exc_mod.BRDFRetrievalFailed, exc_mod.VCD43NRTError)
    assert issubclass(exc_mod.BRDFRetrievalFailed, RuntimeError)


def test_backwards_compatible_exception_reexports():
    names = [
        "VCD43NRTError",
        "CMRServerUnreachable",
        "DownloadFailed",
        "AuxiliaryDownloadFailed",
        "VIIRSUnavailableError",
        "BRDFRetrievalFailed",
    ]
    for name in names:
        expected = getattr(exc_mod, name)
        assert getattr(pkg, name) is expected
        assert getattr(viirs_mod, name) is expected
        assert getattr(proc_mod, name) is expected


def test_brdf_parameters_wraps_singular_solver_error(tmp_path, monkeypatch):
    class FakeVIIRS:
        PRODUCT_SHORT_NAME = "VNP09GA"
        mission = "SNPP"
        mission_priority = ("SNPP",)
        fallback_mode = False

        def granule(self, date_UTC, tile):
            raise exc_mod.VIIRSUnavailableError("unavailable")

    monkeypatch.setattr(proc_mod, "VNP09GA", lambda **kwargs: FakeVIIRS())
    processor = proc_mod.VCD43NRT(
        working_directory=str(tmp_path),
        GEOS5FP_connection=object(),
    )

    target_date = date(2024, 10, 30)
    tile = "h08v05"
    shape = (2, 2)

    class FakeGeometry:
        def __init__(self, shape):
            self.shape = shape

    class FakeGranule:
        def geometry(self, band):
            return FakeGeometry(shape)

    def fake_vnp09ga(d, t):
        if d != target_date:
            raise exc_mod.VIIRSUnavailableError("unavailable")
        return FakeGranule()

    monkeypatch.setattr(processor, "VNP09GA", fake_vnp09ga)

    for var in ["M1", "M_solar_zenith", "M_sensor_zenith", "M_relative_azimuth", "M_solar_zenith_noon"]:
        d = tmp_path / "VCD43NRT_staging" / tile / var
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{target_date:%Y-%m-%d}_{var}.tif").touch()

    monkeypatch.setattr(proc_mod, "_read_brdf_stack", lambda *args, **kwargs: np.zeros((4, 1), dtype=np.float64))
    monkeypatch.setattr(proc_mod.Raster, "open", staticmethod(lambda filename: np.zeros(shape, dtype=np.float64)))
    monkeypatch.setattr(
        proc_mod,
        "nrt_brdf_all",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("singular BRDF fit for pixel 0")),
    )

    with pytest.raises(exc_mod.BRDFRetrievalFailed, match="singular BRDF fit"):
        processor.BRDF_parameters(date_UTC=target_date, tile=tile, band="M1")


def test_all_nan_mosaic_raises_brdf_retrieval_failed(tmp_path, monkeypatch):
    class FakeVIIRS:
        mission = "SNPP"
        mission_priority = ("SNPP",)
        fallback_mode = False
        resampling = "nearest"

    monkeypatch.setattr(proc_mod, "VNP09GA", lambda **kwargs: FakeVIIRS())
    processor = proc_mod.VCD43NRT(
        working_directory=str(tmp_path),
        GEOS5FP_connection=object(),
    )

    nan_grid = np.full((2, 2), np.nan, dtype=np.float32)

    class FakeRaster:
        def __init__(self, arr):
            self.arr = arr
            self.geometry = SimpleNamespace(cell_size=500.0)
            self.cmap = None

        def __array__(self, dtype=None):
            return np.asarray(self.arr, dtype=dtype)

        def to_geometry(self, geometry, resampling=None):
            return self

    class FakeOutGranule:
        NDVI = FakeRaster(nan_grid)
        albedo = FakeRaster(nan_grid)

    fake_geom = SimpleNamespace(
        boundary_latlon=SimpleNamespace(geometry=object()),
        cell_size=500.0,
    )
    monkeypatch.setattr(proc_mod, "find_modland_tiles", lambda g: ["h08v05"])
    monkeypatch.setattr(processor, "granule", lambda date_UTC, tile: FakeOutGranule())
    monkeypatch.setattr(proc_mod.rasters, "where", lambda cond, a, b: b)

    with pytest.raises(exc_mod.BRDFRetrievalFailed, match="NDVI mosaic contains no valid observations"):
        processor.NDVI(date_UTC=date(2024, 10, 30), geometry=fake_geom)

    with pytest.raises(exc_mod.BRDFRetrievalFailed, match="albedo mosaic contains no valid observations"):
        processor.albedo(date_UTC=date(2024, 10, 30), geometry=fake_geom)
