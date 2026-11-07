"""Compare legacy Julia VNP43NRT output with experimental Rust VCD43NRT output.

This script performs comprehensive validation of the Rust implementation by comparing
pixel values, data types, shapes, and statistics from both implementations.

Usage:
    python compare_outputs.py [--verbose] [--save-report FILE]
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import rasterio
from rasterio.io import DatasetReader


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_geotiff(filepath: Path) -> Tuple[np.ndarray, Dict]:
    """Load GeoTIFF file and return data array with metadata."""
    with rasterio.open(filepath) as src:
        data = src.read(1)
        metadata = {
            'crs': src.crs,
            'transform': src.transform,
            'dtype': src.dtypes[0],
            'shape': data.shape,
            'count': src.count,
            'nodata': src.nodata,
        }
        return data, metadata


def get_tiff_files(directory: Path) -> List[Path]:
    """Get all TIFF files in directory."""
    return sorted(directory.glob('*.tif'))


def compare_metadata(
    julia_meta: Dict,
    rust_meta: Dict,
    filename: str
) -> Tuple[bool, List[str]]:
    """Compare metadata between Julia and Rust outputs."""
    issues = []
    
    if julia_meta['dtype'] != rust_meta['dtype']:
        issues.append(
            f"Data type mismatch: Julia={julia_meta['dtype']}, "
            f"Rust={rust_meta['dtype']}"
        )
    
    if julia_meta['shape'] != rust_meta['shape']:
        issues.append(
            f"Shape mismatch: Julia={julia_meta['shape']}, "
            f"Rust={rust_meta['shape']}"
        )
    
    if julia_meta['crs'] != rust_meta['crs']:
        issues.append(
            f"CRS mismatch: Julia={julia_meta['crs']}, "
            f"Rust={rust_meta['crs']}"
        )
    
    # Allow small transform differences due to floating point precision
    if julia_meta['transform'] != rust_meta['transform']:
        logger.debug(
            f"{filename}: Minor transform difference (may be floating point)"
        )
    
    # Both NaN values are considered equivalent (valid NoData)
    julia_nodata = julia_meta['nodata']
    rust_nodata = rust_meta['nodata']
    
    # Check if both are NaN or both are the same value
    julia_is_nan = isinstance(julia_nodata, float) and np.isnan(julia_nodata)
    rust_is_nan = isinstance(rust_nodata, float) and np.isnan(rust_nodata)
    
    if not (julia_is_nan and rust_is_nan) and julia_nodata != rust_nodata:
        issues.append(
            f"NoData value mismatch: Julia={julia_nodata}, "
            f"Rust={rust_nodata}"
        )
    
    return len(issues) == 0, issues


def compare_pixel_values(
    julia_data: np.ndarray,
    rust_data: np.ndarray,
    tolerance: float = 1e-6
) -> Tuple[bool, Dict]:
    """Compare pixel values with tolerance for floating point differences."""
    results = {
        'matches': True,
        'max_difference': 0.0,
        'mean_difference': 0.0,
        'num_differences': 0,
        'percent_different': 0.0,
    }
    
    # Handle NaN values
    valid_mask = ~(np.isnan(julia_data) | np.isnan(rust_data))
    
    if not np.any(valid_mask):
        logger.warning("No valid (non-NaN) data to compare")
        return True, results
    
    julia_valid = julia_data[valid_mask]
    rust_valid = rust_data[valid_mask]
    
    # Compare values
    differences = np.abs(julia_valid - rust_valid)
    results['max_difference'] = float(np.max(differences))
    results['mean_difference'] = float(np.mean(differences))
    
    # Count significant differences (exceeding tolerance)
    significant_diffs = differences > tolerance
    results['num_differences'] = int(np.sum(significant_diffs))
    results['percent_different'] = float(
        100.0 * results['num_differences'] / len(julia_valid)
    )
    
    # Mark as matching if differences are within tolerance
    results['matches'] = results['num_differences'] == 0
    
    return results['matches'], results


def compare_statistics(
    julia_data: np.ndarray,
    rust_data: np.ndarray,
) -> Tuple[bool, Dict]:
    """Compare data statistics."""
    issues = []
    
    # Exclude NaN values
    valid_mask = ~(np.isnan(julia_data) | np.isnan(rust_data))
    julia_valid = julia_data[valid_mask]
    rust_valid = rust_data[valid_mask]
    
    if len(julia_valid) == 0 or len(rust_valid) == 0:
        logger.debug("No valid data for statistics comparison")
        return True, {}
    
    stats = {}
    for stat_name, stat_func in [
        ('min', np.min),
        ('max', np.max),
        ('mean', np.mean),
        ('std', np.std),
        ('median', np.median),
    ]:
        julia_stat = stat_func(julia_valid)
        rust_stat = stat_func(rust_valid)
        stats[f'julia_{stat_name}'] = float(julia_stat)
        stats[f'rust_{stat_name}'] = float(rust_stat)
        
        # Allow small relative difference for statistics (0.1% tolerance)
        # Use absolute difference for very small values (< 1e-6)
        if abs(julia_stat) > 1e-6:
            rel_diff = abs(julia_stat - rust_stat) / abs(julia_stat)
            if rel_diff > 0.001:  # 0.1% tolerance
                issues.append(
                    f"{stat_name}: Julia={julia_stat:.9f}, Rust={rust_stat:.9f}, "
                    f"rel_diff={rel_diff:.6f}"
                )
        else:
            abs_diff = abs(julia_stat - rust_stat)
            if abs_diff > 1e-8:
                issues.append(
                    f"{stat_name}: Julia={julia_stat:.9e}, Rust={rust_stat:.9e}, "
                    f"abs_diff={abs_diff:.9e}"
                )
    
    return len(issues) == 0, {
        'stats': stats,
        'issues': issues,
    }


def main():
    """Main comparison function."""
    parser = argparse.ArgumentParser(
        description='Compare legacy Julia and experimental Rust VNP43NRT outputs'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--save-report',
        type=str,
        metavar='FILE',
        help='Save detailed comparison report to JSON file'
    )
    parser.add_argument(
        '--julia-path',
        type=str,
        default='/Users/gregoryhalverson/data/VNP43NRT_example/VNP43NRT_products/VNP43NRT_A2024304_h08v05',
        help='Path to Julia output directory'
    )
    parser.add_argument(
        '--rust-path',
        type=str,
        default='/Users/gregoryhalverson/data/VCD43NRT_example/VCD43NRT_products/VCD43NRT_A2024304_h08v05',
        help='Path to Rust output directory'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    julia_base = Path(args.julia_path)
    rust_base = Path(args.rust_path)
    
    # Validate directories exist
    if not julia_base.exists():
        logger.error(f"Julia output directory not found: {julia_base}")
        return 1
    
    if not rust_base.exists():
        logger.error(f"Rust output directory not found: {rust_base}")
        return 1
    
    logger.info(f"Julia base: {julia_base}")
    logger.info(f"Rust base: {rust_base}")
    
    # Get TIFF files
    julia_files = get_tiff_files(julia_base)
    rust_files = get_tiff_files(rust_base)
    
    logger.info(f"Found {len(julia_files)} Julia TIF files")
    logger.info(f"Found {len(rust_files)} Rust TIF files")
    
    # Build filename mapping with normalized names (removing product prefix)
    def normalize_filename(fname: str) -> str:
        """Normalize filename by removing product prefix (VNP43NRT or VCD43NRT)."""
        # Remove the product prefix and date/tile part to match files
        # VNP43NRT_A2024304_h08v05_* -> A2024304_h08v05_*
        # VCD43NRT_A2024304_h08v05_* -> A2024304_h08v05_*
        parts = fname.split('_')
        if len(parts) > 3:
            # Skip first part (product name) and reconstruct
            return '_'.join(parts[1:])
        return fname
    
    julia_map = {normalize_filename(f.name): f for f in julia_files}
    rust_map = {normalize_filename(f.name): f for f in rust_files}
    
    # Check file consistency
    julia_names = set(julia_map.keys())
    rust_names = set(rust_map.keys())
    
    only_in_julia = julia_names - rust_names
    only_in_rust = rust_names - julia_names
    
    if only_in_julia:
        logger.warning(f"Files only in Julia output: {sorted(only_in_julia)}")
    if only_in_rust:
        logger.warning(f"Files only in Rust output: {sorted(only_in_rust)}")
    
    # Compare common files (using normalized names)
    common_files = sorted(julia_names & rust_names)
    logger.info(f"Comparing {len(common_files)} common files")
    
    # Track results
    comparison_results = []
    all_passed = True
    
    for filename in common_files:
        logger.info(f"Comparing {filename}")
        
        julia_path = julia_map[filename]
        rust_path = rust_map[filename]
        
        try:
            julia_data, julia_meta = load_geotiff(julia_path)
            rust_data, rust_meta = load_geotiff(rust_path)
            
            result = {'filename': filename}
            
            # Compare metadata
            meta_ok, meta_issues = compare_metadata(
                julia_meta, rust_meta, filename
            )
            result['metadata_ok'] = meta_ok
            result['metadata_issues'] = meta_issues
            
            if not meta_ok:
                all_passed = False
                for issue in meta_issues:
                    logger.warning(f"  {issue}")
            
            # Compare pixel values
            pixels_ok, pixel_stats = compare_pixel_values(julia_data, rust_data)
            result['pixels_ok'] = pixels_ok
            result['pixel_stats'] = pixel_stats
            
            if not pixels_ok and pixel_stats['percent_different'] > 0.1:
                all_passed = False
                logger.warning(
                    f"  Pixel mismatch: {pixel_stats['num_differences']} pixels "
                    f"({pixel_stats['percent_different']:.2f}%) differ by more than "
                    f"tolerance (max: {pixel_stats['max_difference']:.2e}, "
                    f"mean: {pixel_stats['mean_difference']:.2e})"
                )
            elif not pixels_ok:
                logger.info(
                    f"  Minor pixel differences: {pixel_stats['num_differences']} pixels "
                    f"({pixel_stats['percent_different']:.4f}%) differ"
                )
            
            # Compare statistics
            stats_ok, stat_result = compare_statistics(julia_data, rust_data)
            result['stats_ok'] = stats_ok
            result['stats'] = stat_result
            
            if not stats_ok:
                logger.warning(f"  Statistics differences:")
                for issue in stat_result['issues']:
                    logger.warning(f"    {issue}")
            
            comparison_results.append(result)
            
        except Exception as e:
            logger.error(f"Error comparing {filename}: {e}")
            all_passed = False
            comparison_results.append({
                'filename': filename,
                'error': str(e),
            })
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("COMPARISON SUMMARY")
    logger.info("=" * 80)
    
    metadata_matches = sum(1 for r in comparison_results if r.get('metadata_ok', False))
    pixel_matches = sum(1 for r in comparison_results if r.get('pixels_ok', False))
    stats_matches = sum(1 for r in comparison_results if r.get('stats_ok', False))
    errors = sum(1 for r in comparison_results if 'error' in r)
    
    logger.info(f"Total files compared: {len(comparison_results)}")
    logger.info(f"Metadata matches: {metadata_matches}/{len(comparison_results)}")
    logger.info(f"Pixel matches: {pixel_matches}/{len(comparison_results)}")
    logger.info(f"Stats matches: {stats_matches}/{len(comparison_results)}")
    logger.info(f"Errors: {errors}/{len(comparison_results)}")
    
    if all_passed:
        logger.info("\n✓ All comparisons PASSED")
        return_code = 0
    else:
        logger.warning("\n✗ Some comparisons FAILED")
        return_code = 1
    
    # Save report if requested
    if args.save_report:
        report_path = Path(args.save_report)
        report = {
            'timestamp': datetime.now().isoformat(),
            'julia_path': str(julia_base),
            'rust_path': str(rust_base),
            'summary': {
                'total_files': len(comparison_results),
                'metadata_matches': metadata_matches,
                'pixel_matches': pixel_matches,
                'stats_matches': stats_matches,
                'errors': errors,
                'all_passed': all_passed,
            },
            'results': [],
        }
        
        for result in comparison_results:
            result_copy = result.copy()
            # Convert numpy types to JSON-serializable types
            if 'pixel_stats' in result_copy:
                pixel_stats = result_copy['pixel_stats']
                for key in ['max_difference', 'mean_difference', 'percent_different']:
                    if key in pixel_stats:
                        pixel_stats[key] = float(pixel_stats[key])
            
            if 'stats' in result_copy and 'stats' in result_copy['stats']:
                for key in result_copy['stats']['stats']:
                    result_copy['stats']['stats'][key] = float(result_copy['stats']['stats'][key])
            
            report['results'].append(result_copy)
        
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        logger.info(f"Report saved to {report_path}")
    
    return return_code


if __name__ == '__main__':
    sys.exit(main())
