"""Exercise the installed Rust BRDF solver and its terminal progress bar."""

import argparse
from time import perf_counter

import numpy as np

from VCD43NRT import _brdf_rust


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pixels",
        type=positive_int,
        default=250_000,
        help="number of synthetic pixels (default: %(default)s)",
    )
    args = parser.parse_args()

    observations = 12
    solar_row = np.linspace(20.0, 43.1, observations)
    sensor_row = np.linspace(3.0, 17.3, observations)
    azimuth_row = np.linspace(8.0, 85.0, observations)
    reflectance_row = (
        0.3 + 0.2 * np.sin(np.deg2rad(solar_row))
        - 0.1 * np.cos(np.deg2rad(azimuth_row))
    )
    reflectance, solar, sensor, azimuth = (
        np.tile(row, (args.pixels, 1))
        for row in (reflectance_row, solar_row, sensor_row, azimuth_row)
    )
    solar_noon = np.full(args.pixels, 35.0)

    print(f"Rust extension: {_brdf_rust.__file__}")
    print(f"Running {args.pixels:,} pixels with {observations} observations each", flush=True)
    start = perf_counter()
    result = _brdf_rust.nrt_brdf_all(
        reflectance, solar, sensor, azimuth, solar_noon, weighted=False, progress=True
    )
    elapsed = perf_counter() - start
    print()

    if result.shape != (args.pixels, 9) or result.dtype != np.float64:
        raise AssertionError(f"Unexpected Rust solver output: shape={result.shape}, dtype={result.dtype}")
    if not np.isfinite(result).all() or not np.all(result[:, 8] == observations):
        raise AssertionError("Rust solver returned invalid output or observation counts")
    np.testing.assert_allclose(
        result[:, :3],
        np.broadcast_to([0.7703093409891094, 0.5759216223658212, 0.351240991836615], (args.pixels, 3)),
        rtol=1e-7,
        atol=1e-9,
    )
    print(f"Rust BRDF progress test passed in {elapsed:.2f} seconds")


if __name__ == "__main__":
    main()
