import importlib

import numpy as np
import pytest

from VCD43NRT import VCD43NRT, VNP43NRT
from VCD43NRT._brdf_rust import nrt_brdf_all
from VCD43NRT.VCD43NRT import _read_brdf_stack


def _synthetic_inputs():
    observations = 12
    solar_zenith = np.empty((1, observations), dtype=np.float64)
    sensor_zenith = np.empty_like(solar_zenith)
    relative_azimuth = np.empty_like(solar_zenith)
    reflectance = np.empty_like(solar_zenith)

    for index in range(observations):
        solar = 20.0 + index * 2.1
        sensor = 3.0 + index * 1.3
        azimuth = 8.0 + index * 7.0
        solar_zenith[0, index] = solar
        sensor_zenith[0, index] = sensor
        relative_azimuth[0, index] = azimuth
        reflectance[0, index] = 0.3 + 0.2 * _kvol(solar, sensor, azimuth) - 0.1 * _kgeo(
            solar, sensor, azimuth
        )

    return reflectance, solar_zenith, sensor_zenith, relative_azimuth, np.array([35.0])


def _kvol(sz, vz, rz):
    scale = np.pi / 180.0
    epsilon = np.arccos(
        np.clip(
            np.cos(scale * sz) * np.cos(scale * vz)
            + np.sin(scale * sz) * np.sin(scale * vz) * np.cos(scale * rz),
            -1,
            1,
        )
    )
    return (
        ((np.pi / 2 - epsilon) * np.cos(epsilon) + np.sin(epsilon))
        / (np.cos(scale * sz) + np.cos(scale * vz))
        - np.pi / 4
    )


def _kgeo(sz, vz, rz):
    scale = np.pi / 180.0
    sz, vz, rz = sz * scale, vz * scale, rz * scale
    epsilon = np.arccos(
        np.clip(np.cos(sz) * np.cos(vz) + np.sin(sz) * np.sin(vz) * np.cos(rz), -1, 1)
    )
    distance = np.sqrt(np.tan(sz) ** 2 + np.tan(vz) ** 2 - 2 * np.tan(sz) * np.tan(vz) * np.cos(rz))
    cost = 2 * np.sqrt(distance**2 + (np.tan(sz) * np.tan(vz) * np.sin(rz)) ** 2) / (
        1 / np.cos(sz) + 1 / np.cos(vz)
    )
    t = np.arccos(np.clip(cost, -1, 1))
    overlap = max((t - np.sin(t) * np.cos(t)) / np.pi * (1 / np.cos(sz) + 1 / np.cos(vz)), 0)
    return (
        overlap
        - 1 / np.cos(sz)
        - 1 / np.cos(vz)
        + 0.5 * (1 + np.cos(epsilon)) / np.cos(sz) / np.cos(vz)
    )


def test_pyo3_solver_returns_expected_shape_and_finite_solution():
    inputs = _synthetic_inputs()
    results = nrt_brdf_all(*inputs, weighted=False)

    assert results.shape == (1, 9)
    assert results.dtype == np.float64
    assert np.isfinite(results[0, :8]).all()
    assert results[0, 8] == 12
    coefficients = np.array([0.3, 0.2, -0.1])
    noon_radians = np.deg2rad(35.0)
    bsa = np.array(
        [
            1.0,
            -0.007574 - 0.070987 * noon_radians**2 + 0.307588 * noon_radians**3,
            -1.284909 - 0.166314 * noon_radians**2 + 0.041840 * noon_radians**3,
        ]
    )
    nadir = np.array([1.0, _kvol(35.0, 0.0, 0.0), _kgeo(35.0, 0.0, 0.0)])
    expected = [
        np.dot([1.0, 0.189184, -1.377622], coefficients),
        np.dot(bsa, coefficients),
        np.dot(nadir, coefficients),
    ]
    assert results[0, :3] == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize("weighted", [False, True])
def test_pyo3_results_match_independent_numpy_reference(weighted):
    y, sz, vz, rz, noon = _synthetic_inputs()
    actual = nrt_brdf_all(y, sz, vz, rz, noon, weighted=weighted, scale=1.87)[0]
    valid = np.isfinite(y[0])
    x = np.vstack(
        [
            np.ones(valid.sum()),
            np.array([_kvol(a, b, c) for a, b, c in zip(sz[0, valid], vz[0, valid], rz[0, valid])]),
            np.array([_kgeo(a, b, c) for a, b, c in zip(sz[0, valid], vz[0, valid], rz[0, valid])]),
        ]
    )
    observations = y[0, valid]
    weights = np.exp(-0.5 * np.arange(y.shape[1] - 1, -1, -1)[valid] / 1.87) if weighted else np.ones(valid.sum())
    covariance = np.linalg.inv((x * weights) @ x.T)
    coefficients = covariance @ (x * weights) @ observations
    predicted = coefficients @ x
    residual = observations - predicted
    standard_error = np.sqrt(np.sum((residual * weights) ** 2) / (valid.sum() - 3))
    noon_radians = noon[0] * np.pi / 180
    wsa = np.array([1.0, 0.189184, -1.377622])
    bsa = np.array(
        [
            1.0,
            -0.007574 - 0.070987 * noon_radians**2 + 0.307588 * noon_radians**3,
            -1.284909 - 0.166314 * noon_radians**2 + 0.041840 * noon_radians**3,
        ]
    )
    nadir = np.array([1.0, _kvol(noon[0], 0.0, 0.0), _kgeo(noon[0], 0.0, 0.0)])
    variance = np.var(observations, ddof=1)
    expected = np.array(
        [
            np.dot(wsa, coefficients),
            np.dot(bsa, coefficients),
            np.dot(nadir, coefficients),
            standard_error * np.sqrt(wsa @ covariance @ wsa),
            standard_error * np.sqrt(bsa @ covariance @ bsa),
            standard_error * np.sqrt(nadir @ covariance @ nadir),
            standard_error,
            1 - standard_error**2 * (valid.sum() - 3) / variance / valid.sum(),
            valid.sum(),
        ]
    )

    assert actual == pytest.approx(expected, rel=1e-10, abs=1e-12)


def test_pyo3_solver_rejects_mismatched_shapes_and_invalid_scale():
    reflectance, sz, vz, rz, noon = _synthetic_inputs()

    with pytest.raises(ValueError, match="identical shapes"):
        nrt_brdf_all(reflectance, sz[:, :-1], vz, rz, noon)

    with pytest.raises(ValueError, match="scale"):
        nrt_brdf_all(reflectance, sz, vz, rz, noon, scale=0.0)


def test_non_contiguous_input_is_rejected_explicitly():
    reflectance, sz, vz, rz, noon = _synthetic_inputs()
    with pytest.raises(ValueError, match="C-contiguous"):
        nrt_brdf_all(reflectance[:, ::-1], sz, vz, rz, noon)


def test_singular_pixel_fit_fails_with_pixel_index():
    observations = 8
    y = np.ascontiguousarray(np.linspace(0.1, 0.8, observations).reshape(1, -1))
    angle = np.full((1, observations), 20.0)
    with pytest.raises(ValueError, match="pixel 0"):
        nrt_brdf_all(y, angle, angle, angle, np.array([35.0]))


@pytest.mark.parametrize("weighted", [False, True])
def test_missing_observations_and_minimum_count(weighted):
    y, sz, vz, rz, noon = _synthetic_inputs()
    y = np.repeat(y, 3, axis=0)
    sz, vz, rz = (np.repeat(angle, 3, axis=0) for angle in (sz, vz, rz))
    y[0, :6] = np.nan
    y[1, :5] = np.nan
    y[2, 0] = np.inf

    result = nrt_brdf_all(y, sz, vz, rz, np.repeat(noon, 3), weighted=weighted)

    assert np.isnan(result[0]).all()
    assert np.isfinite(result[1:]).all()
    assert result[1:, 8].tolist() == [7, 11]
    assert result[1:, :3] == pytest.approx(
        np.tile(nrt_brdf_all(*_synthetic_inputs(), weighted=weighted)[0, :3], (2, 1)),
        abs=1e-8,
    )


def test_empty_pixels_and_invalid_binding_arguments():
    empty = np.empty((0, 12), dtype=np.float64)
    assert nrt_brdf_all(empty, empty, empty, empty, np.empty(0)).shape == (0, 9)

    y, sz, vz, rz, noon = _synthetic_inputs()
    with pytest.raises(ValueError, match="soz_noon length"):
        nrt_brdf_all(y, sz, vz, rz, np.empty(0))
    with pytest.raises(ValueError, match="at least one observation"):
        nrt_brdf_all(y[:, :0], sz[:, :0], vz[:, :0], rz[:, :0], noon)
    for scale in (-1.0, np.nan, np.inf):
        with pytest.raises(ValueError, match="scale"):
            nrt_brdf_all(y, sz, vz, rz, noon, scale=scale)
    with pytest.raises(TypeError):
        nrt_brdf_all(y.astype(np.float32), sz, vz, rz, noon)


def test_singular_fit_reports_global_pixel_across_chunks():
    y, sz, vz, rz, noon = _synthetic_inputs()
    pixels = 4097
    y, sz, vz, rz = (np.repeat(array, pixels, axis=0) for array in (y, sz, vz, rz))
    sz[-1] = vz[-1] = rz[-1] = 20.0
    with pytest.raises(ValueError, match="pixel 4096"):
        nrt_brdf_all(y, sz, vz, rz, np.repeat(noon, pixels))


def test_rebranded_api_and_legacy_class_alias():
    assert VCD43NRT.__name__ == "VCD43NRT"
    assert issubclass(VNP43NRT, VCD43NRT)


def test_canonical_directory_arguments_and_legacy_aliases(tmp_path, monkeypatch):
    processing_module = importlib.import_module("VCD43NRT.VCD43NRT")

    class FakeVIIRS:
        mission = "SNPP"
        mission_priority = ("SNPP",)
        fallback_mode = False

    monkeypatch.setattr(processing_module, "VNP09GA", lambda **kwargs: FakeVIIRS())
    output_directory = tmp_path / "products"
    staging_directory = tmp_path / "staging"
    processor = VCD43NRT(
        working_directory=str(tmp_path),
        VCD43NRT_directory=str(output_directory),
        VCD43NRT_staging_directory=str(staging_directory),
        GEOS5FP_connection=object(),
    )
    assert processor.VCD43NRT_directory == str(output_directory)
    assert processor.VCD43NRT_staging_directory == str(staging_directory)
    assert processor.VCD43NRT_directory == processor.VCD43NRT_directory

    with pytest.warns(DeprecationWarning, match="VNP43NRT is deprecated"):
        legacy_class = VNP43NRT(
            working_directory=str(tmp_path),
            GEOS5FP_connection=object(),
        )
    assert isinstance(legacy_class, VCD43NRT)

    with pytest.warns(DeprecationWarning, match="deprecated"):
        legacy = VCD43NRT(
            working_directory=str(tmp_path),
            VNP43NRT_directory=str(output_directory),
            VNP43NRT_staging_directory=str(staging_directory),
            GEOS5FP_connection=object(),
        )
    assert legacy.VCD43NRT_directory == str(output_directory)


def test_staged_stack_uses_shared_date_order_and_checks_shapes(tmp_path, monkeypatch):
    from datetime import date

    dates = [date(2026, 10, 1), date(2026, 10, 2)]
    for processing_date in dates:
        filename = tmp_path / f"{processing_date:%Y-%m-%d}_M1.tif"
        filename.touch()

    raster_values = {
        str(tmp_path / "2026-10-01_M1.tif"): np.array([[1, 2], [3, 4]]),
        str(tmp_path / "2026-10-02_M1.tif"): np.array([[5, 6], [7, 8]]),
    }
    processing_module = importlib.import_module("VCD43NRT.VCD43NRT")
    monkeypatch.setattr(
        processing_module.Raster,
        "open",
        staticmethod(lambda filename: raster_values[str(filename)]),
    )

    stack = _read_brdf_stack(str(tmp_path), dates, "M1", (2, 2))
    assert stack.tolist() == [[1, 5], [2, 6], [3, 7], [4, 8]]
    assert stack.flags.c_contiguous

    raster_values[str(tmp_path / "2026-10-02_M1.tif")] = np.ones((1, 4))
    with pytest.raises(ValueError, match="expected \\(2, 2\\)"):
        _read_brdf_stack(str(tmp_path), dates, "M1", (2, 2))
    with pytest.raises(FileNotFoundError, match="missing staged M2 raster for 2026-10-01"):
        _read_brdf_stack(str(tmp_path), dates, "M2", (2, 2))
