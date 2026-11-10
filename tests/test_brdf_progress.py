"""Tests for BRDF progress callback functionality."""

import numpy as np
import pytest

from VCD43NRT import _brdf_rust, brdf_progress


class TestProgressCallbacks:
    """Test suite for progress callback integration."""

    @pytest.fixture
    def sample_data(self):
        """Create sample BRDF input data."""
        pixels = 10000
        observations = 12

        solar_row = np.linspace(20.0, 43.1, observations)
        sensor_row = np.linspace(3.0, 17.3, observations)
        azimuth_row = np.linspace(8.0, 85.0, observations)
        reflectance_row = (
            0.3
            + 0.2 * np.sin(np.deg2rad(solar_row))
            - 0.1 * np.cos(np.deg2rad(azimuth_row))
        )

        return {
            "pixels": pixels,
            "observations": observations,
            "reflectance": np.tile(reflectance_row, (pixels, 1)),
            "solar": np.tile(solar_row, (pixels, 1)),
            "sensor": np.tile(sensor_row, (pixels, 1)),
            "azimuth": np.tile(azimuth_row, (pixels, 1)),
            "solar_noon": np.full(pixels, 35.0),
        }

    def test_no_progress(self, sample_data):
        """Test BRDF solver without progress display."""
        result = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=False,
        )
        assert result.shape == (sample_data["pixels"], 9)
        assert result.dtype == np.float64

    def test_indicatif_progress(self, sample_data):
        """Test BRDF solver with built-in Indicatif progress."""
        result = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=True,
        )
        assert result.shape == (sample_data["pixels"], 9)
        assert result.dtype == np.float64

    def test_simple_callback(self, sample_data):
        """Test BRDF solver with simple text callback."""
        updates = []

        def simple_callback(current, total):
            updates.append((current, total))

        result = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=True,
            progress_callback=simple_callback,
        )

        assert result.shape == (sample_data["pixels"], 9)
        assert len(updates) > 0
        # Verify updates are in ascending order
        for i in range(len(updates) - 1):
            assert updates[i][0] <= updates[i + 1][0]
        # Verify final update matches total
        assert updates[-1][0] == sample_data["pixels"]
        assert updates[-1][1] == sample_data["pixels"]

    def test_make_simple_callback(self, sample_data, capsys):
        """Test make_simple_callback utility."""
        callback = brdf_progress.make_simple_callback("TEST")

        result = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=True,
            progress_callback=callback,
        )

        captured = capsys.readouterr()
        assert "TEST" in captured.out
        assert result.shape == (sample_data["pixels"], 9)

    def test_tqdm_context_manager(self, sample_data):
        """Test TqdmProgressCallback context manager."""
        with brdf_progress.TqdmProgressCallback(
            total=sample_data["pixels"], desc="Test"
        ) as callback:
            assert callable(callback)
            result = _brdf_rust.nrt_brdf_all(
                sample_data["reflectance"],
                sample_data["solar"],
                sample_data["sensor"],
                sample_data["azimuth"],
                sample_data["solar_noon"],
                weighted=False,
                progress=True,
                progress_callback=callback,
            )

        assert result.shape == (sample_data["pixels"], 9)

    def test_custom_callback(self, sample_data):
        """Test make_custom_callback utility."""
        updates = []

        def my_update(current, total):
            updates.append((current, total))

        callback = brdf_progress.make_custom_callback(my_update)

        result = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=True,
            progress_callback=callback,
        )

        assert result.shape == (sample_data["pixels"], 9)
        assert len(updates) > 0

    def test_none_callback_with_progress_true(self, sample_data):
        """Test that None callback works with progress=True (uses Indicatif)."""
        result = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=True,
            progress_callback=None,
        )
        assert result.shape == (sample_data["pixels"], 9)

    def test_callback_receives_correct_values(self, sample_data):
        """Test that callback receives correct current/total values."""
        received_totals = set()
        received_currents = []

        def tracking_callback(current, total):
            received_totals.add(total)
            received_currents.append(current)

        result = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=True,
            progress_callback=tracking_callback,
        )

        # Should have only one unique total value
        assert len(received_totals) == 1
        assert received_totals.pop() == sample_data["pixels"]

        # Currents should be strictly increasing
        assert received_currents[-1] == sample_data["pixels"]
        for i in range(len(received_currents) - 1):
            assert received_currents[i] <= received_currents[i + 1]

    def test_output_consistency_across_progress_modes(self, sample_data):
        """Verify output is identical regardless of progress mode."""
        result_no_progress = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=False,
        )

        updates = []

        def tracking_callback(current, total):
            updates.append((current, total))

        result_with_callback = _brdf_rust.nrt_brdf_all(
            sample_data["reflectance"],
            sample_data["solar"],
            sample_data["sensor"],
            sample_data["azimuth"],
            sample_data["solar_noon"],
            weighted=False,
            progress=True,
            progress_callback=tracking_callback,
        )

        # Results should be identical
        np.testing.assert_array_equal(result_no_progress, result_with_callback)

    def test_callback_with_small_dataset(self, sample_data):
        """Test callback with dataset smaller than chunk size."""
        small_data = {
            k: v[: 100] if isinstance(v, np.ndarray) and v.ndim > 0 else v
            for k, v in sample_data.items()
        }
        small_data["pixels"] = 100

        updates = []

        def tracking_callback(current, total):
            updates.append((current, total))

        result = _brdf_rust.nrt_brdf_all(
            small_data["reflectance"],
            small_data["solar"],
            small_data["sensor"],
            small_data["azimuth"],
            small_data["solar_noon"],
            weighted=False,
            progress=True,
            progress_callback=tracking_callback,
        )

        assert result.shape == (100, 9)
        assert len(updates) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
