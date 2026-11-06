use ndarray::Array2;
use numpy::{IntoPyArray, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Number of output bands/metrics per pixel:
/// 0: WSA (White-Sky Albedo / bi-hemispherical reflectance)
/// 1: BSA (Black-Sky Albedo / directional-hemispherical reflectance at solar noon)
/// 2: NBAR (Nadir BRDF-Adjusted Reflectance at solar noon)
/// 3: WSA Standard Error
/// 4: BSA Standard Error
/// 5: NBAR Standard Error
/// 6: BRDF Model Root Mean Square Error (RMSE / standard error of regression)
/// 7: Coefficient of Determination (R²)
/// 8: Valid observation count (nt)
const OUTPUT_COLUMNS: usize = 9;

/// Chunk size in pixels for processing to maintain cache locality and bounded memory
const PIXEL_CHUNK_SIZE: usize = 4096;

/// References to viewing geometry and reflectance arrays passed into chunk processing.
struct ChunkInput<'a> {
    reflectance: &'a [f64],
    solar_zenith: &'a [f64],
    sensor_zenith: &'a [f64],
    relative_azimuth: &'a [f64],
    solar_noon: &'a [f64],
    pixel_count: usize,
    observation_count: usize,
    first_pixel: usize,
}

/// RossThick volumetric scattering kernel ($K_{\text{vol}}$).
///
/// Models diffuse single and multiple scattering from a semi-infinite dense canopy
/// with horizontally uniform leaf area index (LAI), isotropic leaf orientation,
/// and Lambertian scattering elements.
///
/// Formula:
/// $$K_{\text{vol}}(\theta_s, \theta_v, \phi) = \frac{(\pi/2 - \xi)\cos\xi + \sin\xi}{\cos\theta_s + \cos\theta_v} - \frac{\pi}{4}$$
/// where the scattering phase angle $\xi$ is given by:
/// $$\cos\xi = \cos\theta_s \cos\theta_v + \sin\theta_s \sin\theta_v \cos\phi$$
///
/// Normalized such that $K_{\text{vol}}(0, 0, 0) = 0$.
///
/// References:
/// - Roujean, J.-L., Leroy, M., & Deschamps, P.-Y. (1992). A bidirectional reflectance
///   model of the Earth's surface for the correction of remote sensing data.
///   Journal of Geophysical Research: Atmospheres, 97(D18), 20455-20468.
/// - Wanner, W., Li, X., & Strahler, A. H. (1995). On the derivation of kernels for
///   kernel-driven models of bidirectional reflectance. Journal of Geophysical Research:
///   Atmospheres, 100(D10), 21077-21089.
#[inline]
fn kvol(sz: f64, vz: f64, rz: f64) -> f64 {
    let scale = std::f64::consts::PI / 180.0;
    let (sz, vz, rz) = (sz * scale, vz * scale, rz * scale);

    // Unclamped cos_epsilon replicates Julia's `Kvol_sc` acos behavior exactly
    let cos_epsilon = sz.cos() * vz.cos() + sz.sin() * vz.sin() * rz.cos();
    let epsilon = cos_epsilon.acos();

    ((std::f64::consts::FRAC_PI_2 - epsilon) * epsilon.cos() + epsilon.sin())
        / (sz.cos() + vz.cos())
        - std::f64::consts::FRAC_PI_4
}

/// LiSparse-Reciprocal geometric shadow casting kernel ($K_{\text{geo}}$).
///
/// Models geometric-optical mutual shadowing of discrete plant crowns on a flat surface,
/// assuming randomly distributed, spheroidal sunlit and shaded crowns with height-to-center
/// parameter $h/b = 2$ and crown shape parameter $b/r = 1$.
///
/// Reciprocal formulation derived by Wanner et al. (1995) to satisfy Helmholtz reciprocity
/// between solar and viewing angles.
///
/// Overlap area function:
/// $$O(\theta_s, \theta_v, \phi) = \frac{1}{\pi} (t - \sin t \cos t) \left(\frac{1}{\cos\theta_s} + \frac{1}{\cos\theta_v}\right)$$
/// where:
/// $$\cos t = 2 \frac{\sqrt{D^2 + (\tan\theta_s \tan\theta_v \sin\phi)^2}}{\sec\theta_s + \sec\theta_v}$$
/// $$D = \sqrt{\tan^2\theta_s + \tan^2\theta_v - 2\tan\theta_s\tan\theta_v\cos\phi}$$
///
/// Kernel expression:
/// $$K_{\text{geo}} = O - \sec\theta_s - \sec\theta_v + \frac{1}{2}(1 + \cos\xi)\sec\theta_s\sec\theta_v$$
///
/// Normalized such that $K_{\text{geo}}(0, 0, 0) = 0$.
///
/// References:
/// - Li, X., & Strahler, A. H. (1992). Geometric-optical bidirectional reflectance modeling
///   of the discrete-crown vegetation canopy: Effect of crown shape and mutual shadowing.
///   IEEE Transactions on Geoscience and Remote Sensing, 30(2), 276-292.
/// - Wanner, W., et al. (1995). On the derivation of kernels for kernel-driven models
///   of bidirectional reflectance. JGR: Atmospheres, 100(D10), 21077-21089.
/// - Lucht, W., Schaaf, C. B., & Strahler, A. H. (2000). An algorithm for the retrieval of
///   albedo from space using semiempirical BRDF models. IEEE TGRS, 38(2), 977-998.
#[inline]
fn kgeo(sz: f64, vz: f64, rz: f64) -> f64 {
    let scale = std::f64::consts::PI / 180.0;
    let (sz, vz, rz) = (sz * scale, vz * scale, rz * scale);
    let tan_sz = sz.tan();
    let tan_vz = vz.tan();
    let sec_sz = 1.0 / sz.cos();
    let sec_vz = 1.0 / vz.cos();

    let cos_epsilon = sz.cos() * vz.cos() + sz.sin() * vz.sin() * rz.cos();
    let distance = (tan_sz.powi(2) + tan_vz.powi(2) - 2.0 * tan_sz * tan_vz * rz.cos()).sqrt();

    // cos_t clamping matches Julia's `clamp(cost, -1.0, 1.0)` in Kgeo_sc
    let cos_t = (2.0 * (distance.powi(2) + (tan_sz * tan_vz * rz.sin()).powi(2)).sqrt()
        / (sec_sz + sec_vz))
        .clamp(-1.0, 1.0);
    let t = cos_t.acos();
    let overlap = ((t - t.sin() * t.cos()) / std::f64::consts::PI * (sec_sz + sec_vz)).max(0.0);

    overlap - sec_sz - sec_vz + 0.5 * (1.0 + cos_epsilon) * sec_sz * sec_vz
}

/// Analytical matrix inversion for symmetric / non-singular 3x3 normal systems $(X^T W X)^{-1}$.
///
/// Inverts via cofactor expansion, matching LAPACK `dgetrf`/`dgetri` behavior in Julia:
/// rejects exactly zero or non-finite determinants, while propagating continuous floating-point
/// values without hard threshold divergence.
#[inline]
fn inverse_3x3(matrix: [[f64; 3]; 3]) -> Option<[[f64; 3]; 3]> {
    let [[a, b, c], [d, e, f], [g, h, i]] = matrix;
    let cofactors = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ];
    let determinant = a * cofactors[0][0] + b * cofactors[1][0] + c * cofactors[2][0];

    // Replicate Julia LAPACK behavior: fail only on non-finite or exact zero determinant
    if !determinant.is_finite() || determinant == 0.0 {
        return None;
    }

    let inverse = cofactors.map(|row| row.map(|value| value / determinant));
    if inverse.iter().flatten().all(|value| value.is_finite()) {
        Some(inverse)
    } else {
        None
    }
}

#[inline]
fn dot3(left: [f64; 3], right: [f64; 3]) -> f64 {
    left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

#[inline]
fn mat_vec(matrix: [[f64; 3]; 3], vector: [f64; 3]) -> [f64; 3] {
    matrix.map(|row| dot3(row, vector))
}

/// Process a chunk of pixels using the Near-Real-Time (NRT) weighted linear kernel BRDF inversion.
///
/// Mathematical Inversion:
/// Solves the linear system:
/// $$R(\theta_s, \theta_v, \phi) = f_{\text{iso}} + f_{\text{vol}} K_{\text{vol}}(\theta_s, \theta_v, \phi) + f_{\text{geo}} K_{\text{geo}}(\theta_s, \theta_v, \phi)$$
/// using weighted least-squares with exponential temporal decay:
/// $$w_j = \exp\left(-0.5 \cdot \frac{p - 1 - j}{\text{scale}}\right)$$
///
/// Albedo Calculations:
/// 1. White-Sky Albedo (WSA - bi-hemispherical reflectance):
///    $$a_{\text{ws}} = f_{\text{iso}} + 0.189184 f_{\text{vol}} - 1.377622 f_{\text{geo}}$$
/// 2. Black-Sky Albedo (BSA - directional-hemispherical reflectance at solar noon $\theta_0$):
///    $$a_{\text{bs}}(\theta_0) = f_{\text{iso}} + f_{\text{vol}} \left(g_{0\text{vol}} + g_{1\text{vol}} \theta_0^2 + g_{2\text{vol}} \theta_0^3\right) + f_{\text{geo}} \left(g_{0\text{geo}} + g_{1\text{geo}} \theta_0^2 + g_{2\text{geo}} \theta_0^3\right)$$
/// 3. Nadir BRDF-Adjusted Reflectance (NBAR):
///    $$R_{\text{nbar}} = f_{\text{iso}} + f_{\text{vol}} K_{\text{vol}}(\theta_0, 0, 0) + f_{\text{geo}} K_{\text{geo}}(\theta_0, 0, 0)$$
///
/// References:
/// - Schaaf, C. B., et al. (2002). First operational BRDF, albedo nadir reflectance products
///   from MODIS. Remote Sensing of Environment, 83(1-2), 135-148.
/// - Wang, Z., et al. (2018). The MODIS Version 6 BRDF/albedo product (MCD43).
///   Remote Sensing of Environment, 206, 23-40.
fn process_chunk(input: ChunkInput<'_>, weighted: bool, scale: f64) -> Vec<f64> {
    let ChunkInput {
        reflectance,
        solar_zenith,
        sensor_zenith,
        relative_azimuth,
        solar_noon,
        pixel_count,
        observation_count,
        first_pixel: _,
    } = input;

    let mut output = vec![f64::NAN; pixel_count * OUTPUT_COLUMNS];

    // Polynomial constants for BSA hemispherical integrals (Lucht et al., 2000; Schaaf et al., 2002)
    let g0vol = -0.007574;
    let g1vol = -0.070987;
    let g2vol = 0.307588;

    let g0geo = -1.284909;
    let g1geo = -0.166314;
    let g2geo = 0.041840;

    let rad = std::f64::consts::PI / 180.0;

    for pixel in 0..pixel_count {
        let y_start = pixel * observation_count;
        let y = &reflectance[y_start..y_start + observation_count];
        let count = y.iter().filter(|value| value.is_finite()).count();

        // Operational quality requirement: Minimum 7 cloud-free / clear observations (MOD43/VNP43 standard)
        if count < 7 {
            continue;
        }

        let mut normal = [[0.0; 3]; 3];
        let mut rhs = [0.0; 3];
        let mut reflectance_sum = 0.0;

        for (observation, &value) in y.iter().enumerate() {
            if !value.is_finite() {
                continue;
            }
            let offset = y_start + observation;
            let feature = [
                1.0,
                kvol(
                    solar_zenith[offset],
                    sensor_zenith[offset],
                    relative_azimuth[offset],
                ),
                kgeo(
                    solar_zenith[offset],
                    sensor_zenith[offset],
                    relative_azimuth[offset],
                ),
            ];

            let weight = if weighted {
                (-0.5 * ((observation_count - 1 - observation) as f64) / scale).exp()
            } else {
                1.0
            };

            reflectance_sum += value;
            for row in 0..3 {
                rhs[row] += weight * feature[row] * value;
                for column in 0..3 {
                    normal[row][column] += weight * feature[row] * feature[column];
                }
            }
        }

        // On singular inversion, preserve NaN for this pixel row without aborting the chunk,
        // mirroring Julia's per-pixel NaN propagation.
        let Some(covariance) = inverse_3x3(normal) else {
            continue;
        };

        let coefficients = mat_vec(covariance, rhs);
        let mean = reflectance_sum / count as f64;
        let mut residual_sum = 0.0;
        let mut variance_sum = 0.0;

        for (observation, &observed) in y.iter().enumerate() {
            if !observed.is_finite() {
                continue;
            }
            let offset = y_start + observation;
            let feature = [
                1.0,
                kvol(
                    solar_zenith[offset],
                    sensor_zenith[offset],
                    relative_azimuth[offset],
                ),
                kgeo(
                    solar_zenith[offset],
                    sensor_zenith[offset],
                    relative_azimuth[offset],
                ),
            ];

            let predicted = dot3(coefficients, feature);
            let residual = observed - predicted;
            let weight = if weighted {
                (-0.5 * ((observation_count - 1 - observation) as f64) / scale).exp()
            } else {
                1.0
            };

            // Weighted residual sum of squares: sum((e_i * w_i)^2) to match VNP43NRT.jl
            residual_sum += if weighted {
                (residual * weight).powi(2)
            } else {
                residual.powi(2)
            };
            variance_sum += (observed - mean).powi(2);
        }

        // Standard error of regression (RMSE) with nt - 3 degrees of freedom
        let standard_error = (residual_sum / (count - 3) as f64).sqrt();

        // Sample variance with N - 1 degrees of freedom (matching Julia var(ytt; corrected=true))
        let variance = variance_sum / (count - 1) as f64;

        // BRDF coefficient of determination (R²)
        let r_squared = 1.0 - standard_error.powi(2) * (count - 3) as f64 / variance / count as f64;

        // Solar noon angle for black-sky albedo and nadir BRDF-adjusted reflectance (NBAR)
        let solar_noon_radians = solar_noon[pixel] * rad;
        let bsa = [
            1.0,
            g0vol + g1vol * solar_noon_radians.powi(2) + g2vol * solar_noon_radians.powi(3),
            g0geo + g1geo * solar_noon_radians.powi(2) + g2geo * solar_noon_radians.powi(3),
        ];
        let nadir = [
            1.0,
            kvol(solar_noon[pixel], 0.0, 0.0),
            kgeo(solar_noon[pixel], 0.0, 0.0),
        ];
        let wsa = [1.0, 0.189184, -1.377622];

        // Standard error of albedo quantities via error propagation: SE * sqrt(g^T * Cov * g)
        // Matching Julia dot(g, Si * g)
        let bsa_cov = dot3(bsa, mat_vec(covariance, bsa));
        let wsa_cov = dot3(wsa, mat_vec(covariance, wsa));
        let nadir_cov = dot3(nadir, mat_vec(covariance, nadir));

        let bsa_se = bsa_cov.sqrt() * standard_error;
        let wsa_se = wsa_cov.sqrt() * standard_error;
        let nadir_se = nadir_cov.sqrt() * standard_error;

        let row = &mut output[pixel * OUTPUT_COLUMNS..(pixel + 1) * OUTPUT_COLUMNS];
        row[0] = dot3(wsa, coefficients);
        row[1] = dot3(bsa, coefficients);
        row[2] = dot3(nadir, coefficients);
        row[3] = wsa_se;
        row[4] = bsa_se;
        row[5] = nadir_se;
        row[6] = standard_error;
        row[7] = r_squared;
        row[8] = count as f64;
    }

    output
}

fn ensure_standard_layout<T>(array: &ndarray::ArrayView2<'_, T>, name: &str) -> PyResult<()> {
    if !array.is_standard_layout() {
        return Err(PyValueError::new_err(format!(
            "{name} must be a C-contiguous array"
        )));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
#[pyfunction(signature = (y, sz, vz, rz, soz_noon, weighted=true, scale=1.87))]
fn nrt_brdf_all<'py>(
    py: Python<'py>,
    y: PyReadonlyArray2<'py, f64>,
    sz: PyReadonlyArray2<'py, f64>,
    vz: PyReadonlyArray2<'py, f64>,
    rz: PyReadonlyArray2<'py, f64>,
    soz_noon: PyReadonlyArray1<'py, f64>,
    weighted: bool,
    scale: f64,
) -> PyResult<Bound<'py, numpy::PyArray2<f64>>> {
    let y_view = y.as_array();
    let sz_view = sz.as_array();
    let vz_view = vz.as_array();
    let rz_view = rz.as_array();
    let noon_view = soz_noon.as_array();
    ensure_standard_layout(&y_view, "y")?;
    ensure_standard_layout(&sz_view, "sz")?;
    ensure_standard_layout(&vz_view, "vz")?;
    ensure_standard_layout(&rz_view, "rz")?;

    let dimensions = y_view.dim();
    if sz_view.dim() != dimensions || vz_view.dim() != dimensions || rz_view.dim() != dimensions {
        return Err(PyValueError::new_err(
            "y, sz, vz, and rz must have identical shapes",
        ));
    }
    if noon_view.len() != dimensions.0 {
        return Err(PyValueError::new_err(
            "soz_noon length must equal the number of pixel rows",
        ));
    }
    if dimensions.1 == 0 {
        return Err(PyValueError::new_err(
            "input arrays must contain at least one observation",
        ));
    }
    if !scale.is_finite() || scale <= 0.0 {
        return Err(PyValueError::new_err(
            "scale must be finite and greater than zero",
        ));
    }

    let y_slice = y.as_slice()?;
    let sz_slice = sz.as_slice()?;
    let vz_slice = vz.as_slice()?;
    let rz_slice = rz.as_slice()?;
    let noon_slice = soz_noon.as_slice()?;
    let (pixel_count, observation_count) = dimensions;
    let mut results = Vec::with_capacity(pixel_count * OUTPUT_COLUMNS);

    for start in (0..pixel_count).step_by(PIXEL_CHUNK_SIZE) {
        let chunk_pixels = PIXEL_CHUNK_SIZE.min(pixel_count - start);
        let start_observation = start * observation_count;
        let end_observation = (start + chunk_pixels) * observation_count;
        let y_chunk = y_slice[start_observation..end_observation].to_vec();
        let sz_chunk = sz_slice[start_observation..end_observation].to_vec();
        let vz_chunk = vz_slice[start_observation..end_observation].to_vec();
        let rz_chunk = rz_slice[start_observation..end_observation].to_vec();
        let noon_chunk = noon_slice[start..start + chunk_pixels].to_vec();

        let computed = py.allow_threads(move || {
            process_chunk(
                ChunkInput {
                    reflectance: &y_chunk,
                    solar_zenith: &sz_chunk,
                    sensor_zenith: &vz_chunk,
                    relative_azimuth: &rz_chunk,
                    solar_noon: &noon_chunk,
                    pixel_count: chunk_pixels,
                    observation_count,
                    first_pixel: start,
                },
                weighted,
                scale,
            )
        });
        results.extend(computed);
    }

    let result_array = Array2::from_shape_vec((pixel_count, OUTPUT_COLUMNS), results)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(result_array.into_pyarray(py))
}

#[pymodule]
fn _brdf_rust(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(nrt_brdf_all, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nadir_kernels_are_zero() {
        assert!(kvol(0.0, 0.0, 0.0).abs() < 1e-12);
        assert!(kgeo(0.0, 0.0, 0.0).abs() < 1e-12);
    }

    #[test]
    fn weighted_and_unweighted_fit_produce_finite_nadir_and_counts() {
        let observations = 12;
        let mut y = Vec::with_capacity(observations);
        let mut sz = Vec::with_capacity(observations);
        let mut vz = Vec::with_capacity(observations);
        let mut rz = Vec::with_capacity(observations);
        for index in 0..observations {
            let i = index as f64;
            let solar = 20.0 + i * 2.1;
            let sensor = 3.0 + i * 1.3;
            let azimuth = 8.0 + i * 7.0;
            sz.push(solar);
            vz.push(sensor);
            rz.push(azimuth);
            y.push(0.3 + 0.2 * kvol(solar, sensor, azimuth) - 0.1 * kgeo(solar, sensor, azimuth));
        }
        let noon = [35.0];
        for weighted in [false, true] {
            let result = process_chunk(
                ChunkInput {
                    reflectance: &y,
                    solar_zenith: &sz,
                    sensor_zenith: &vz,
                    relative_azimuth: &rz,
                    solar_noon: &noon,
                    pixel_count: 1,
                    observation_count: observations,
                    first_pixel: 0,
                },
                weighted,
                1.87,
            );
            assert_eq!(result.len(), OUTPUT_COLUMNS);
            assert!(result[..8].iter().all(|value| value.is_finite()));
            assert_eq!(result[8], observations as f64);
        }
    }

    #[test]
    fn fewer_than_seven_finite_observations_returns_nan_row() {
        let result = process_chunk(
            ChunkInput {
                reflectance: &[0.2, f64::NAN, 0.3, f64::NAN, 0.4, f64::NAN],
                solar_zenith: &[10.0; 6],
                sensor_zenith: &[2.0; 6],
                relative_azimuth: &[5.0; 6],
                solar_noon: &[30.0],
                pixel_count: 1,
                observation_count: 6,
                first_pixel: 0,
            },
            true,
            1.87,
        );
        assert!(result.iter().all(|value| value.is_nan()));
    }

    #[test]
    fn singular_matrix_propagates_nan_without_error() {
        let observations = 8;
        let y = vec![0.3; observations];
        let sz = vec![30.0; observations];
        let vz = vec![0.0; observations];
        let rz = vec![0.0; observations];
        let noon = [30.0];

        let result = process_chunk(
            ChunkInput {
                reflectance: &y,
                solar_zenith: &sz,
                sensor_zenith: &vz,
                relative_azimuth: &rz,
                solar_noon: &noon,
                pixel_count: 1,
                observation_count: observations,
                first_pixel: 0,
            },
            true,
            1.87,
        );
        assert_eq!(result.len(), OUTPUT_COLUMNS);
        assert!(result[0].is_nan());
        assert!(result[1].is_nan());
    }
}