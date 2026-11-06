import numpy as np

from VCD43NRT import BRDF


def test_statistical_radiative_transport_uses_lookup_bins(monkeypatch):
    table = np.arange(90 * 50).reshape(90, 50)
    monkeypatch.setattr(BRDF, "loadtxt", lambda filename: table)

    actual = BRDF.statistical_radiative_transport(
        np.array([0.0, 45.0, 90.0]), np.array([0.0, 0.1, 0.97])
    )

    np.testing.assert_array_equal(actual, [table[1, 1], table[46, 6], table[89, 49]])


def test_bidirectional_reflectance_clips_and_fills_inputs(monkeypatch):
    def lookup(sza, aot):
        np.testing.assert_allclose(sza, [30.0, 30.0, 30.0])
        np.testing.assert_allclose(aot, [0.1, 0.97, 0.535])
        return np.array([0.0, 0.5, 1.0])

    monkeypatch.setattr(BRDF, "statistical_radiative_transport", lookup)
    result = BRDF.bidirectional_reflectance(
        np.array([0.8, 0.8, 0.8]),
        np.array([0.2, 0.2, 0.2]),
        np.array([30.0, -1.0, 100.0]),
        np.array([0.0, 2.0, np.nan]),
    )
    np.testing.assert_allclose(result, [0.2, 0.5, 0.8])


def test_statistical_radiative_transport_data_is_present():
    result = BRDF.statistical_radiative_transport(np.array([45.0]), np.array([0.1]))
    assert result.shape == (1,)
    assert np.isfinite(result).all()
