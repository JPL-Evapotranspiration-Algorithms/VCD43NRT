from datetime import date
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

import importlib

proc_mod = importlib.import_module("VCD43NRT.VCD43NRT")
viirs_mod = importlib.import_module("VCD43NRT.VIIRS")
from VCD43NRT.BRDF import bidirectional_reflectance
from VCD43NRT.exceptions import DownloadFailed


class FakeGranuleRecord:
    def __init__(self, filename: str):
        self._filename = filename

    def data_links(self):
        return [f"https://example.org/{self._filename}"]

    def __getitem__(self, key):
        if key == "umm":
            return {"TemporalExtent": {"RangeDateTime": {"BeginningDateTime": "2024-10-30T10:30:00Z"}}}
        raise KeyError(key)


def test_download_granules_validates_hdf5_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(viirs_mod, "login", lambda: object())
    monkeypatch.setattr(viirs_mod, "RETRIES", 2)

    client = viirs_mod.VIIRS(working_directory=str(tmp_path))
    fname = "VNP09GA.A2024304.h08v05.002.2024305000000.h5"
    granule = FakeGranuleRecord(fname)

    attempts = {"count": 0}

    def fake_download(granules, local_path):
        attempts["count"] += 1
        target = Path(local_path) / fname
        if attempts["count"] == 1:
            # First attempt writes corrupt non-HDF5 bytes
            target.write_bytes(b"not-an-hdf5-file")
        else:
            # Second attempt writes a valid HDF5 file
            with h5py.File(target, "w") as handle:
                handle.create_dataset("ok", data=np.array([1, 2, 3]))
        return [str(target)]

    monkeypatch.setattr(viirs_mod.earthaccess, "download", fake_download)

    paths = client.download_granules([granule])
    assert attempts["count"] == 2
    assert len(paths) == 1
    assert Path(paths[0]).exists()
    # Temporary staging directory should be cleaned up
    tmp_root = Path(client.download_directory) / "tmp"
    assert list(tmp_root.iterdir()) == []


def test_download_granules_cleans_up_nonempty_temp_dir_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(viirs_mod, "login", lambda: object())
    monkeypatch.setattr(viirs_mod, "RETRIES", 1)

    client = viirs_mod.VIIRS(working_directory=str(tmp_path))
    fname = "VNP09GA.A2024304.h08v05.002.2024305000000.h5"
    granule = FakeGranuleRecord(fname)

    def fake_download(granules, local_path):
        leftover = Path(local_path) / "partial.tmp"
        leftover.write_bytes(b"partial")
        return [RuntimeError("connection reset")]

    monkeypatch.setattr(viirs_mod.earthaccess, "download", fake_download)

    with pytest.raises(DownloadFailed, match="connection reset"):
        client.download_granules([granule])

    tmp_root = Path(client.download_directory) / "tmp"
    assert list(tmp_root.iterdir()) == []


def test_vcd43nrt_skips_reprocessing_when_granule_complete(tmp_path, monkeypatch):
    class FakeVIIRS:
        mission = "SNPP"
        mission_priority = ("SNPP",)
        fallback_mode = False

    monkeypatch.setattr(proc_mod, "VNP09GA", lambda **kwargs: FakeVIIRS())
    processor = proc_mod.VCD43NRT(
        working_directory=str(tmp_path),
        GEOS5FP_connection=object(),
    )

    target_date = date(2024, 10, 30)
    tile = "h08v05"
    granule_dir = Path(processor.granule_directory(target_date, tile))
    granule_dir.mkdir(parents=True, exist_ok=True)

    granule_id = processor.granule_ID(target_date, tile)
    required = ["NDVI", "NBAR_I1", "NBAR_I2", "albedo"] + [
        f"{prefix}_M{b}" for b in [1, 2, 3, 4, 5, 7, 8, 10, 11] for prefix in ("BSA", "WSA")
    ]
    for var in required:
        (granule_dir / f"{granule_id}_{var}.tif").touch()

    monkeypatch.setattr(
        processor,
        "BRDF_parameters",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not recompute complete granule")),
    )

    result = processor.VCD43NRT(date_UTC=target_date, tile=tile)
    assert result.complete is True
    assert Path(result.directory) == granule_dir


def test_vcd43nrt_stages_atomically_in_temp_directory(tmp_path, monkeypatch):
    class FakeVIIRS:
        mission = "SNPP"
        mission_priority = ("SNPP",)
        fallback_mode = False

    monkeypatch.setattr(proc_mod, "VNP09GA", lambda **kwargs: FakeVIIRS())
    processor = proc_mod.VCD43NRT(
        working_directory=str(tmp_path),
        GEOS5FP_connection=object(),
    )

    class FakeRaster(np.ndarray):
        def __new__(cls, input_array, geometry=None):
            obj = np.asarray(input_array, dtype=np.float32).view(cls)
            obj.geometry = SimpleNamespace(shape=obj.shape, cell_size=500.0)
            return obj

    monkeypatch.setattr(proc_mod, "Raster", FakeRaster)
    monkeypatch.setattr(proc_mod, "_calculate_sza", lambda doy, hour, geom: FakeRaster(np.full((2, 2), 30.0)))
    monkeypatch.setattr(proc_mod.rasters, "clip", lambda arr, lo, hi: FakeRaster(np.clip(arr, lo, hi)))

    def fake_atomic_write(image, filename, use_cog=False):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).touch()

    monkeypatch.setattr(proc_mod, "_atomic_write_geotiff", fake_atomic_write)
    monkeypatch.setattr(
        proc_mod.VCD43NRTGranule,
        "variable",
        lambda self, name, geometry=None, cmap=None: FakeRaster(np.full((2, 2), 0.5)),
    )
    monkeypatch.setattr(processor, "AOT", lambda **kwargs: FakeRaster(np.full((2, 2), 0.1)))

    seen_temp_dirs = []
    original_add_layer = proc_mod.VCD43NRTGranule.add_layer

    def spy_add_layer(self, variable_name, image):
        seen_temp_dirs.append(self.directory)
        return original_add_layer(self, variable_name, image)

    monkeypatch.setattr(proc_mod.VCD43NRTGranule, "add_layer", spy_add_layer)

    def fake_brdf_params(date_UTC, tile, band):
        r = FakeRaster(np.full((2, 2), 0.25))
        return proc_mod.BRDFParameters(
            WSA=r, BSA=r, NBAR=r, WSA_SE=r, BSA_SE=r, NBAR_SE=r, BRDF_SE=r, BRDF_R2=r, count=r, filter_invalid=False
        )

    monkeypatch.setattr(processor, "BRDF_parameters", fake_brdf_params)

    target_date = date(2024, 10, 30)
    tile = "h08v05"
    granule = processor.VCD43NRT(date_UTC=target_date, tile=tile)

    assert granule.complete is True
    assert len(seen_temp_dirs) > 0
    assert all(".tmp" in d for d in seen_temp_dirs)
    # Temp parent directories should be cleaned up after promotion
    products_dir = Path(processor.VCD43NRT_directory)
    names = sorted(p.name for p in products_dir.iterdir())
    assert processor.granule_ID(target_date, tile) in names
    assert not any(".tmp" in n for n in names)


def test_corrupted_staged_raster_is_removed_and_detected(tmp_path, monkeypatch):
    bad_tif = tmp_path / "2024-10-30_M1.tif"
    bad_tif.write_bytes(b"corrupt-tiff-header")

    def fake_open(filename):
        raise OSError("not recognized as being in a supported file format")

    monkeypatch.setattr(proc_mod.Raster, "open", staticmethod(fake_open))

    assert proc_mod._is_valid_staged_raster(str(bad_tif), expected_shape=(2, 2)) is False
    assert not bad_tif.exists()


def test_bidirectional_reflectance_handles_all_nan_inputs():
    wsa = np.array([[0.3, 0.3]])
    bsa = np.array([[0.2, 0.2]])
    sza = np.array([[np.nan, np.nan]])
    aot = np.array([[np.nan, np.nan]])
    out = bidirectional_reflectance(wsa, bsa, sza, aot)
    assert out.shape == (1, 2)
    assert np.isfinite(out).all()
