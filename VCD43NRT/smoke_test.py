"""Check the installed Rust extension with a small deterministic BRDF fit."""

import numpy as np

from . import _brdf_rust


def main():
    observations = 12
    solar = np.linspace(20.0, 43.1, observations).reshape(1, -1)
    sensor = np.linspace(3.0, 17.3, observations).reshape(1, -1)
    azimuth = np.linspace(8.0, 85.0, observations).reshape(1, -1)
    reflectance = 0.3 + 0.2 * np.sin(np.deg2rad(solar)) - 0.1 * np.cos(np.deg2rad(azimuth))

    result = _brdf_rust.nrt_brdf_all(
        reflectance, solar, sensor, azimuth, np.array([35.0]), weighted=False
    )
    if result.shape != (1, 9) or result.dtype != np.float64:
        raise AssertionError(f"Unexpected Rust solver output: shape={result.shape}, dtype={result.dtype}")
    if not np.isfinite(result).all() or result[0, 8] != observations:
        raise AssertionError(f"Rust solver returned invalid output: {result}")

    # Reference values from an independent NumPy least-squares fit.
    np.testing.assert_allclose(
        result[0, :3],
        [0.7703093409891094, 0.5759216223658212, 0.351240991836615],
        rtol=1e-7,
        atol=1e-9,
    )
    print(f"Rust BRDF smoke test passed: {_brdf_rust.__file__}")


if __name__ == "__main__":
    main()
