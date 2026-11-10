"""Examples of using Python callbacks with the Rust BRDF solver.

This script demonstrates three approaches to progress tracking:
1. Indicatif (built-in Rust progress bar)
2. Python tqdm progress bar via callback
3. Simple custom callback
"""

import argparse
from time import perf_counter
from typing import Optional

import numpy as np

from VCD43NRT import _brdf_rust
from VCD43NRT.brdf_progress import (
    TqdmProgressCallback,
    make_simple_callback,
    make_tqdm_callback,
)


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def test_indicatif(pixels: int, observations: int) -> float:
    """Test using Rust's built-in Indicatif progress bar."""
    print("\n=== Test 1: Indicatif (Rust built-in) ===")
    print(f"Running {pixels:,} pixels with {observations} observations each")

    solar_row = np.linspace(20.0, 43.1, observations)
    sensor_row = np.linspace(3.0, 17.3, observations)
    azimuth_row = np.linspace(8.0, 85.0, observations)
    reflectance_row = (
        0.3
        + 0.2 * np.sin(np.deg2rad(solar_row))
        - 0.1 * np.cos(np.deg2rad(azimuth_row))
    )
    reflectance, solar, sensor, azimuth = (
        np.tile(row, (pixels, 1)) for row in (reflectance_row, solar_row, sensor_row, azimuth_row)
    )
    solar_noon = np.full(pixels, 35.0)

    start = perf_counter()
    result = _brdf_rust.nrt_brdf_all(
        reflectance, solar, sensor, azimuth, solar_noon, weighted=False, progress=True
    )
    elapsed = perf_counter() - start

    if result.shape != (pixels, 9) or result.dtype != np.float64:
        raise AssertionError(f"Unexpected output: shape={result.shape}, dtype={result.dtype}")
    print(f"✓ Completed in {elapsed:.2f} seconds\n")
    return elapsed


def test_tqdm_callback(pixels: int, observations: int) -> float:
    """Test using Python tqdm via callback."""
    print("=== Test 2: tqdm (Python callback) ===")
    print(f"Running {pixels:,} pixels with {observations} observations each")

    solar_row = np.linspace(20.0, 43.1, observations)
    sensor_row = np.linspace(3.0, 17.3, observations)
    azimuth_row = np.linspace(8.0, 85.0, observations)
    reflectance_row = (
        0.3
        + 0.2 * np.sin(np.deg2rad(solar_row))
        - 0.1 * np.cos(np.deg2rad(azimuth_row))
    )
    reflectance, solar, sensor, azimuth = (
        np.tile(row, (pixels, 1)) for row in (reflectance_row, solar_row, sensor_row, azimuth_row)
    )
    solar_noon = np.full(pixels, 35.0)

    start = perf_counter()
    with TqdmProgressCallback(total=pixels, desc="BRDF (tqdm)", unit="pixel") as progress_cb:
        result = _brdf_rust.nrt_brdf_all(
            reflectance,
            solar,
            sensor,
            azimuth,
            solar_noon,
            weighted=False,
            progress=True,
            progress_callback=progress_cb,
        )
    elapsed = perf_counter() - start

    if result.shape != (pixels, 9) or result.dtype != np.float64:
        raise AssertionError(f"Unexpected output: shape={result.shape}, dtype={result.dtype}")
    print(f"✓ Completed in {elapsed:.2f} seconds\n")
    return elapsed


def test_simple_callback(pixels: int, observations: int) -> float:
    """Test using simple text callback."""
    print("=== Test 3: Simple Callback (text output) ===")
    print(f"Running {pixels:,} pixels with {observations} observations each")

    solar_row = np.linspace(20.0, 43.1, observations)
    sensor_row = np.linspace(3.0, 17.3, observations)
    azimuth_row = np.linspace(8.0, 85.0, observations)
    reflectance_row = (
        0.3
        + 0.2 * np.sin(np.deg2rad(solar_row))
        - 0.1 * np.cos(np.deg2rad(azimuth_row))
    )
    reflectance, solar, sensor, azimuth = (
        np.tile(row, (pixels, 1)) for row in (reflectance_row, solar_row, sensor_row, azimuth_row)
    )
    solar_noon = np.full(pixels, 35.0)

    progress_cb = make_simple_callback("BRDF")

    start = perf_counter()
    result = _brdf_rust.nrt_brdf_all(
        reflectance,
        solar,
        sensor,
        azimuth,
        solar_noon,
        weighted=False,
        progress=True,
        progress_callback=progress_cb,
    )
    elapsed = perf_counter() - start

    if result.shape != (pixels, 9) or result.dtype != np.float64:
        raise AssertionError(f"Unexpected output: shape={result.shape}, dtype={result.dtype}")
    print(f"\n✓ Completed in {elapsed:.2f} seconds\n")
    return elapsed


def test_no_progress(pixels: int, observations: int) -> float:
    """Test with no progress display."""
    print("=== Test 4: No Progress Display ===")
    print(f"Running {pixels:,} pixels with {observations} observations each")

    solar_row = np.linspace(20.0, 43.1, observations)
    sensor_row = np.linspace(3.0, 17.3, observations)
    azimuth_row = np.linspace(8.0, 85.0, observations)
    reflectance_row = (
        0.3
        + 0.2 * np.sin(np.deg2rad(solar_row))
        - 0.1 * np.cos(np.deg2rad(azimuth_row))
    )
    reflectance, solar, sensor, azimuth = (
        np.tile(row, (pixels, 1)) for row in (reflectance_row, solar_row, sensor_row, azimuth_row)
    )
    solar_noon = np.full(pixels, 35.0)

    start = perf_counter()
    result = _brdf_rust.nrt_brdf_all(
        reflectance, solar, sensor, azimuth, solar_noon, weighted=False, progress=False
    )
    elapsed = perf_counter() - start

    if result.shape != (pixels, 9) or result.dtype != np.float64:
        raise AssertionError(f"Unexpected output: shape={result.shape}, dtype={result.dtype}")
    print(f"✓ Completed in {elapsed:.2f} seconds\n")
    return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pixels",
        type=positive_int,
        default=50_000,
        help="number of synthetic pixels (default: %(default)s)",
    )
    parser.add_argument(
        "--observations",
        type=positive_int,
        default=12,
        help="number of temporal observations (default: %(default)s)",
    )
    parser.add_argument(
        "--test",
        type=str,
        choices=["all", "indicatif", "tqdm", "simple", "none"],
        default="all",
        help="which test to run (default: %(default)s)",
    )
    args = parser.parse_args()

    tests = {
        "indicatif": test_indicatif,
        "tqdm": test_tqdm_callback,
        "simple": test_simple_callback,
        "none": test_no_progress,
    }

    print(f"Rust extension: {_brdf_rust.__file__}")
    print(f"Parameters: pixels={args.pixels:,}, observations={args.observations}")

    results = {}
    if args.test == "all":
        for name, test_func in tests.items():
            results[name] = test_func(args.pixels, args.observations)
    else:
        results[args.test] = tests[args.test](args.pixels, args.observations)

    print("=== Summary ===")
    for name, elapsed in results.items():
        print(f"{name:12s}: {elapsed:.2f}s")


if __name__ == "__main__":
    main()
