#!/usr/bin/env python3
"""Update VCD43NRT packaging, imports, Makefile, and CI for out-of-the-box dev and deployment."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def update_pyproject(repo_root: Path) -> None:
    path = repo_root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")

    # 1. Align requires-python with Rust abi3-py311 and README (Python 3.11+)
    text, count_py = re.subn(
        r'requires-python\s*=\s*">=3\.12"',
        'requires-python = ">=3.11"',
        text,
    )
    if count_py == 0 and 'requires-python = ">=3.11"' not in text:
        raise RuntimeError("Could not locate requires-python in pyproject.toml")

    # 2. Ensure setuptools<81 is declared so earthaccess's pkg_resources import works OOB on Python 3.12-3.14
    if '"setuptools<81"' not in text and "'setuptools<81'" not in text:
        text, count_deps = re.subn(
            r'("earthaccess>=0\.9\.0",)',
            r'\1\n    "setuptools<81",',
            text,
            count=1,
        )
        if count_deps == 0:
            raise RuntimeError("Could not locate earthaccess dependency in pyproject.toml")

    path.write_text(text, encoding="utf-8")
    print(f"[OK] Updated {path}")


def update_vcd43nrt_imports(repo_root: Path) -> None:
    path = repo_root / "VCD43NRT" / "VCD43NRT.py"
    text = path.read_text(encoding="utf-8")

    old_block = """try:
    from ._brdf_rust import nrt_brdf_all
    from .BRDF import bidirectional_reflectance
    from .VIIRSDownloader import VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI
    from .VNP09GA import VNP09GA
    from .VJ109GA import VJ109GA
    from .VNP09GA_granule import VNP09GAGranule
    from .VIIRS_granule import ALBEDO_COLORMAP, NDVI_COLORMAP
    from .VIIRS import VIIRSUnavailableError
    from .VIIRS_missions import normalize_mission
except ImportError:  # pragma: no cover - legacy direct-import fallback
    from _brdf_rust import nrt_brdf_all
    from BRDF import bidirectional_reflectance
    from VIIRSDownloader import VIIRSDownloaderAlbedo, VIIRSDownloaderNDVI
    from VNP09GA import VNP09GA
    from VJ109GA import VJ109GA
    from VNP09GA_granule import VNP09GAGranule
    from VIIRS_granule import ALBEDO_COLORMAP, NDVI_COLORMAP
    from VIIRS import VIIRSUnavailableError
    from VIIRS_missions import normalize_mission"""

    new_block = """if __package__:
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
    from VIIRS_missions import normalize_mission"""

    if old_block in text:
        text = text.replace(old_block, new_block, 1)
    elif "Failed to import compiled Rust extension 'VCD43NRT._brdf_rust'" not in text:
        raise RuntimeError("Could not locate target import block in VCD43NRT/VCD43NRT.py")

    path.write_text(text, encoding="utf-8")
    print(f"[OK] Updated {path}")


def update_init_py(repo_root: Path) -> None:
    path = repo_root / "VCD43NRT" / "__init__.py"
    text = path.read_text(encoding="utf-8")

    guard = """try:
    from . import _brdf_rust
except ImportError as exc:
    raise ImportError(
        "VCD43NRT requires the compiled '_brdf_rust' extension. "
        "Run 'make install' or 'maturin develop --manifest-path rust/Cargo.toml' in a source checkout."
    ) from exc

"""
    if "VCD43NRT requires the compiled '_brdf_rust' extension" not in text:
        text = guard + text
        path.write_text(text, encoding="utf-8")
    print(f"[OK] Updated {path}")


def update_makefile(repo_root: Path) -> None:
    path = repo_root / "makefile"
    text = path.read_text(encoding="utf-8")

    # 1. Consolidate rust-build and install-package to avoid double-building via pip
    old_build_section = """rust-build:
	@echo "Building and installing the Rust extension in development mode..."
	python -m maturin develop --manifest-path rust/Cargo.toml"""

    new_build_section = """rust-build:
	@echo "Building and installing the Rust extension and dev dependencies..."
	python -m maturin develop --manifest-path rust/Cargo.toml --extras dev"""

    if old_build_section in text:
        text = text.replace(old_build_section, new_build_section, 1)

    old_install_section = """install-package: rust-build
	@echo "Installing package in development mode..."
	python -m pip install -e ".[dev]" """.rstrip()

    new_install_section = """install-package: rust-build
	@echo "Development package and Rust extension installed." """.rstrip()

    if old_install_section in text:
        text = text.replace(old_install_section, new_install_section, 1)

    # 2. Ensure test depends on rust-build so stale binaries are never tested
    text = re.sub(
        r"^test:\s*$",
        "test: rust-build",
        text,
        count=1,
        flags=re.MULTILINE,
    )

    # 3. Ensure mamba environment includes pip and setuptools<81 OOB
    old_env = "mamba create -y -n $(ENVIRONMENT_NAME) -c conda-forge python=$(PYTHON_VERSION) rust maturin"
    new_env = 'mamba create -y -n $(ENVIRONMENT_NAME) -c conda-forge python=$(PYTHON_VERSION) pip "setuptools<81" rust maturin'
    if old_env in text:
        text = text.replace(old_env, new_env, 1)

    # 4. Add wheel-test target for isolated deployment verification
    wheel_test_target = """
wheel-test:
	@echo "Building release wheel and running isolated smoke test..."
	rm -rf dist/
	python -m maturin build --release --manifest-path rust/Cargo.toml --out dist
	python -m venv /tmp/vcd43nrt_wheel_verify
	/tmp/vcd43nrt_wheel_verify/bin/pip install --upgrade pip
	/tmp/vcd43nrt_wheel_verify/bin/pip install dist/*.whl
	cd /tmp && /tmp/vcd43nrt_wheel_verify/bin/python -I -m VCD43NRT.smoke_test
	rm -rf /tmp/vcd43nrt_wheel_verify
"""
    if "wheel-test:" not in text:
        text = text.replace(
            ".PHONY: clean rust-clean rust-build rust-rebuild test",
            ".PHONY: clean rust-clean rust-build rust-rebuild test wheel-test",
            1,
        )
        text = text.rstrip() + "\n" + wheel_test_target

    path.write_text(text, encoding="utf-8")
    print(f"[OK] Updated {path}")


def update_ci_workflow(repo_root: Path) -> None:
    ci_path = repo_root / ".github" / "workflows" / "ci.yml"
    ci_path.parent.mkdir(parents=True, exist_ok=True)
    ci_content = """name: CI

on:
  push:
    branches: ["main", "fixing_modland"]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ["3.11", "3.12", "3.13", "3.14"]

    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Set up Rust toolchain
        uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt

      - name: Install build tools
        run: python -m pip install --upgrade pip "maturin>=1.8,<2"

      - name: Check Rust formatting and unit tests
        run: |
          cargo fmt --manifest-path rust/Cargo.toml -- --check
          cargo test --locked --manifest-path rust/Cargo.toml

      - name: Build and install editable package with dev extras
        run: python -m maturin develop --locked --manifest-path rust/Cargo.toml --extras dev

      - name: Run pytest suite
        env:
          SKIP_EARTHDATA_LOGIN: "true"
        run: PYTHONPATH=. pytest

      - name: Verify isolated wheel installation
        run: |
          python -m maturin build --locked --release --manifest-path rust/Cargo.toml --out dist
          python -m venv /tmp/wheel-test-env
          /tmp/wheel-test-env/bin/pip install dist/*.whl
          cd /tmp && /tmp/wheel-test-env/bin/python -I -m VCD43NRT.smoke_test
"""
    ci_path.write_text(ci_content, encoding="utf-8")
    print(f"[OK] Wrote {ci_path}")


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    if not (repo_root / "pyproject.toml").exists() or not (repo_root / "VCD43NRT").exists():
        print("Error: Run this script from the root of the VCD43NRT repository.", file=sys.stderr)
        return 1

    update_pyproject(repo_root)
    update_vcd43nrt_imports(repo_root)
    update_init_py(repo_root)
    update_makefile(repo_root)
    update_ci_workflow(repo_root)

    print("\nAll OOB developer and deployment updates applied successfully.")
    print("Next steps:")
    print("  1. make install")
    print("  2. make test")
    print("  3. make wheel-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
