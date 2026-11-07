use indicatif::{ProgressBar, ProgressStyle};
use ndarray::Array2;
use numpy::{IntoPyArray, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

// Number of output metrics per pixel:
// 0: WSA (White-Sky Albedo / bi-hemispherical reflectance)
// 1: BSA (Black-Sky Albedo / directional-hemispherical reflectance at solar noon)
// 2: NBAR (Nadir BRDF-Adjusted Reflectance at solar noon)
// 3: WSA Standard Error
// 4: BSA Standard Error
// 5: NBAR Standard Error
// 6: BRDF Model Root Mean Square Error (RMSE / standard error of regression)
// 7: Coefficient of Determination (R^2)
// 8: Valid observation count (nt)
const OUTPUT_COLUMNS: usize = 9;

// Number of pixels evaluated per batch to ensure cache locality and manageable thread chunk sizes
const PIXEL_CHUNK_SIZE: usize = 4096;

// Borrows views of input slices for an individual chunk of spatial pixels across the temporal window
struct ChunkInput<'a> {
    // Flattened 1D slice of surface reflectance observations: [pixel_count * observation_count]
    reflectance: &'a [f64],
    // Flattened 1D slice of solar zenith angles in degrees: [pixel_count * observation_count]
    solar_zenith: &'a [f64],
    // Flattened 1D slice of view/sensor zenith angles in degrees: [pixel_count * observation_count]
    sensor_zenith: &'a [f64],
    // Flattened 1D slice of relative azimuth angles in degrees: [pixel_count * observation_count]
    relative_azimuth: &'a [f64],
    // 1D slice of local solar noon zenith angles in degrees for each pixel: [pixel_count]
    solar_noon: &'a [f64],
    // Number of spatial pixels contained in this chunk
    pixel_count: usize,
    // Number of temporal observations (swath acquisitions) per pixel
    observation_count: usize,
    // Zero-based index of the first pixel in this chunk relative to the full raster array
    #[allow(dead_code)]
    first_pixel: usize,
}

// RossThick volumetric scattering kernel (Kvol).
// Models radiative transfer and diffuse scattering through a semi-infinite plane-parallel uniform leaf canopy.
// sz: Solar zenith angle in degrees
// vz: View/sensor zenith angle in degrees
// rz: Relative azimuth angle in degrees
// References:
// - Roujean, Leroy, & Deschamps (1992), JGR Atmospheres, 97(D18), 20455-20468
// - Wanner, Li, & Strahler (1995), JGR Atmospheres, 100(D10), 21077-21089
#[inline]
fn kvol(sz: f64, vz: f64, rz: f64) -> f64 {
    // Multiplier converting angular measurements from degrees to radians
    let scale = std::f64::consts::PI / 180.0;
    // Project all angles from degrees to radians
    let (sz, vz, rz) = (sz * scale, vz * scale, rz * scale);

    // Compute cosine of scattering phase angle xi: cos(xi) = cos(sz)*cos(vz) + sin(sz)*sin(vz)*cos(rz)
    // Left unclamped to strictly replicate the IEEE floating-point behavior of Julia's Kvol_sc
    let cos_epsilon = sz.cos() * vz.cos() + sz.sin() * vz.sin() * rz.cos();
    // Compute scattering phase angle xi in radians via arccosine
    let epsilon = cos_epsilon.acos();

    // Evaluate Roujean RossThick analytical kernel expression:
    // ( (pi/2 - xi)*cos(xi) + sin(xi) ) / (cos(sz) + cos(vz)) - pi/4
    // Normalized to yield 0.0 at nadir view and direct overhead solar illumination (sz=0, vz=0, rz=0)
    ((std::f64::consts::FRAC_PI_2 - epsilon) * epsilon.cos() + epsilon.sin())
        / (sz.cos() + vz.cos())
        - std::f64::consts::FRAC_PI_4
}

// LiSparse-Reciprocal geometric-optical shadow casting kernel (Kgeo).
// Models inter-crown mutual shadowing of discrete spheroidal crowns on a flat ground surface.
// Assumes crown height-to-center ratio h/b = 2 and crown shape ratio b/r = 1.
// sz: Solar zenith angle in degrees
// vz: View/sensor zenith angle in degrees
// rz: Relative azimuth angle in degrees
// References:
// - Li & Strahler (1992), IEEE TGRS, 30(2), 276-292
// - Wanner et al. (1995), JGR Atmospheres, 100(D10), 21077-21089
// - Lucht, Schaaf, & Strahler (2000), IEEE TGRS, 38(2), 977-998
#[inline]
fn kgeo(sz: f64, vz: f64, rz: f64) -> f64 {
    // Multiplier converting angular measurements from degrees to radians
    let scale = std::f64::consts::PI / 180.0;
    // Project all angles from degrees to radians
    let (sz, vz, rz) = (sz * scale, vz * scale, rz * scale);
    // Trigonometric tangent of solar zenith angle
    let tan_sz = sz.tan();
    // Trigonometric tangent of sensor zenith angle
    let tan_vz = vz.tan();
    // Secant (1 / cos) of solar zenith angle
    let sec_sz = 1.0 / sz.cos();
    // Secant (1 / cos) of sensor zenith angle
    let sec_vz = 1.0 / vz.cos();

    // Cosine of the phase angle between solar and sensor directions
    let cos_epsilon = sz.cos() * vz.cos() + sz.sin() * vz.sin() * rz.cos();
    // Projected horizontal distance between sunlit crown center and shadow center
    let distance = (tan_sz.powi(2) + tan_vz.powi(2) - 2.0 * tan_sz * tan_vz * rz.cos()).sqrt();

    // Intermediate parameter determining geometric overlap area of elliptical crown projections
    let cos_t = (2.0 * (distance.powi(2) + (tan_sz * tan_vz * rz.sin()).powi(2)).sqrt()
        / (sec_sz + sec_vz))
        .clamp(-1.0, 1.0); // Clamped to [-1.0, 1.0] matching Julia Kgeo_sc clamp(cost, -1.0, 1.0)
                           // Angle describing proportion of mutual shadow overlap
    let t = cos_t.acos();
    // Normalized overlap area function O(sz, vz, rz)
    let overlap = ((t - t.sin() * t.cos()) / std::f64::consts::PI * (sec_sz + sec_vz)).max(0.0);

    // LiSparse-Reciprocal kernel formula ensuring Helmholtz reciprocity between incoming and outgoing directions:
    // O - sec(sz) - sec(vz) + 0.5 * (1 + cos(xi)) * sec(sz) * sec(vz)
    overlap - sec_sz - sec_vz + 0.5 * (1.0 + cos_epsilon) * sec_sz * sec_vz
}

// Computes the exact closed-form analytical inverse of a 3x3 symmetric matrix via classical cofactor expansion.
// Replicates Julia LAPACK (dgetrf/dgetri) conditions: returns None only on exact zero or non-finite determinants.
#[inline]
fn inverse_3x3(matrix: [[f64; 3]; 3]) -> Option<[[f64; 3]; 3]> {
    // Unpack matrix components into individual scalar variables
    let [[a, b, c], [d, e, f], [g, h, i]] = matrix;

    // Construct the cofactor matrix containing the signed 2x2 sub-determinants
    let cofactors = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ];

    // Compute determinant using Laplace expansion across row 0
    let determinant = a * cofactors[0][0] + b * cofactors[1][0] + c * cofactors[2][0];

    // Reject non-invertible matrices: zero determinant or floating-point overflow/NaN
    if !determinant.is_finite() || determinant == 0.0 {
        return None;
    }

    // Divide transposed cofactor matrix by scalar determinant to yield the inverse
    let inverse = cofactors.map(|row| row.map(|value| value / determinant));

    // Confirm all elements in inverted matrix are finite numerical values
    if inverse.iter().flatten().all(|value| value.is_finite()) {
        Some(inverse)
    } else {
        None
    }
}

// Evaluates 3-element vector inner dot product: left[0]*right[0] + left[1]*right[1] + left[2]*right[2]
#[inline]
fn dot3(left: [f64; 3], right: [f64; 3]) -> f64 {
    left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

// Multiplies a 3x3 matrix by a 3x1 column vector to produce a 3x1 vector
#[inline]
fn mat_vec(matrix: [[f64; 3]; 3], vector: [f64; 3]) -> [f64; 3] {
    matrix.map(|row| dot3(row, vector))
}

// Executes RTLSR linear kernel inversion across a sub-chunk of spatial pixels.
// Uses an exponential decay weight over the observation timeline to emphasize recent surface state changes.
fn process_chunk(input: ChunkInput<'_>, weighted: bool, scale: f64) -> Vec<f64> {
    // Destructure chunk input parameters
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

    // Allocate flat destination vector initialized to NaN for all output metrics across all pixels
    let mut output = vec![f64::NAN; pixel_count * OUTPUT_COLUMNS];

    // Polynomial integration coefficients for RossThick volumetric kernel in Black-Sky Albedo (Lucht et al., 2000)
    let g0vol = -0.007574;
    let g1vol = -0.070987;
    let g2vol = 0.307588;

    // Polynomial integration coefficients for LiSparse-R geometric kernel in Black-Sky Albedo (Lucht et al., 2000)
    let g0geo = -1.284909;
    let g1geo = -0.166314;
    let g2geo = 0.041840;

    // Degree to radian conversion factor
    let rad = std::f64::consts::PI / 180.0;

    // Iterate through every spatial pixel in the assigned chunk
    for pixel in 0..pixel_count {
        // Compute base offset for the current pixel's temporal observation sequence
        let y_start = pixel * observation_count;
        // Slice observation values for this specific pixel
        let y = &reflectance[y_start..y_start + observation_count];
        // Count number of finite (valid, cloud-free, non-masked) observations
        let count = y.iter().filter(|value| value.is_finite()).count();

        // Operational BRDF threshold: require at least 7 valid multi-angle observations (MCD43/VNP43 standard)
        if count < 7 {
            continue;
        }

        // Initialize normal matrix accumulator A = X^T * W * X
        let mut normal = [[0.0; 3]; 3];
        // Initialize right-hand side vector accumulator b = X^T * W * y
        let mut rhs = [0.0; 3];
        // Running sum of valid observed reflectances for mean and variance calculations
        let mut reflectance_sum = 0.0;

        // First pass: accumulate normal equations (X^T * W * X) and (X^T * W * y)
        for (observation, &value) in y.iter().enumerate() {
            // Ignore NaN, Inf, or masked values
            if !value.is_finite() {
                continue;
            }
            // Locate absolute index in geometry arrays
            let offset = y_start + observation;

            // Formulate feature vector: [isotropic constant, volumetric kernel, geometric kernel]
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

            // Calculate temporal exponential weight: w_j = exp(-0.5 * (p - 1 - j) / scale)
            let weight = if weighted {
                (-0.5 * ((observation_count - 1 - observation) as f64) / scale).exp()
            } else {
                1.0
            };

            // Accumulate sum of valid reflectance values
            reflectance_sum += value;

            // Accumulate normal equations
            for row in 0..3 {
                // b_i += w * feature_i * y
                rhs[row] += weight * feature[row] * value;
                for column in 0..3 {
                    // A_ij += w * feature_i * feature_j
                    normal[row][column] += weight * feature[row] * feature[column];
                }
            }
        }

        // Invert the 3x3 normal matrix to retrieve parameter covariance matrix: Cov = (X^T * W * X)^(-1)
        // If collinear viewing geometry renders matrix singular, leave pixel as NaN to mirror Julia behavior
        let Some(covariance) = inverse_3x3(normal) else {
            continue;
        };

        // Solve for BRDF kernel weights: f = Cov * rhs = [f_iso, f_vol, f_geo]
        let coefficients = mat_vec(covariance, rhs);

        // Compute mean of valid observations
        let mean = reflectance_sum / count as f64;
        // Running accumulator for weighted residual sum of squares
        let mut residual_sum = 0.0;
        // Running accumulator for total sum of squares around the sample mean
        let mut variance_sum = 0.0;

        // Second pass: compute residuals, regression RMSE, and sample variance
        for (observation, &observed) in y.iter().enumerate() {
            // Skip non-finite values
            if !observed.is_finite() {
                continue;
            }
            // Locate absolute index in geometry arrays
            let offset = y_start + observation;

            // Reconstruct feature vector for this observation
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

            // Predict surface reflectance using fitted coefficients: y_pred = f^T * feature
            let predicted = dot3(coefficients, feature);
            // Residual: e = y_observed - y_predicted
            let residual = observed - predicted;

            // Compute observation weight
            let weight = if weighted {
                (-0.5 * ((observation_count - 1 - observation) as f64) / scale).exp()
            } else {
                1.0
            };

            // Accumulate weighted squared residual: sum( (e_i * w_i)^2 ) matching VNP43NRT.jl
            residual_sum += if weighted {
                (residual * weight).powi(2)
            } else {
                residual.powi(2)
            };

            // Accumulate sum of squared deviations from sample mean
            variance_sum += (observed - mean).powi(2);
        }

        // Standard error of regression (RMSE) with nt - 3 degrees of freedom (accounting for 3 fitted parameters)
        let standard_error = (residual_sum / (count - 3) as f64).sqrt();
        // Unbiased sample variance with nt - 1 degrees of freedom (matching Julia var(ytt; corrected=true))
        let variance = variance_sum / (count - 1) as f64;
        // Coefficient of determination: R^2 = 1 - (SE^2 * (nt - 3)) / (variance * nt)
        let r_squared = 1.0 - standard_error.powi(2) * (count - 3) as f64 / variance / count as f64;

        // Solar noon zenith angle in radians for local midday albedo estimation
        let solar_noon_radians = solar_noon[pixel] * rad;

        // Black-Sky Albedo directional-hemispherical integral vector at local solar noon:
        // g_bsa = [1.0, g0vol + g1vol*szn^2 + g2vol*szn^3, g0geo + g1geo*szn^2 + g2geo*szn^3]
        let bsa = [
            1.0,
            g0vol + g1vol * solar_noon_radians.powi(2) + g2vol * solar_noon_radians.powi(3),
            g0geo + g1geo * solar_noon_radians.powi(2) + g2geo * solar_noon_radians.powi(3),
        ];

        // Nadir BRDF-Adjusted Reflectance kernel vector at local solar noon and 0-degree view zenith:
        // g_nbar = [1.0, Kvol(szn, 0, 0), Kgeo(szn, 0, 0)]
        let nadir = [
            1.0,
            kvol(solar_noon[pixel], 0.0, 0.0),
            kgeo(solar_noon[pixel], 0.0, 0.0),
        ];

        // White-Sky Albedo bi-hemispherical integral vector (Lucht et al., 2000):
        // g_wsa = [1.0, 0.189184, -1.377622]
        let wsa = [1.0, 0.189184, -1.377622];

        // Propagate parameter covariance into albedo variance: Var(a) = g^T * Cov * g
        let bsa_cov = dot3(bsa, mat_vec(covariance, bsa));
        let wsa_cov = dot3(wsa, mat_vec(covariance, wsa));
        let nadir_cov = dot3(nadir, mat_vec(covariance, nadir));

        // Standard errors of derived products: SE * sqrt(g^T * Cov * g)
        let bsa_se = bsa_cov.sqrt() * standard_error;
        let wsa_se = wsa_cov.sqrt() * standard_error;
        let nadir_se = nadir_cov.sqrt() * standard_error;

        // Populate destination output row for the current pixel
        let row = &mut output[pixel * OUTPUT_COLUMNS..(pixel + 1) * OUTPUT_COLUMNS];
        row[0] = dot3(wsa, coefficients); // Band 0: White-Sky Albedo (WSA)
        row[1] = dot3(bsa, coefficients); // Band 1: Black-Sky Albedo (BSA)
        row[2] = dot3(nadir, coefficients); // Band 2: Nadir BRDF-Adjusted Reflectance (NBAR)
        row[3] = wsa_se; // Band 3: WSA Standard Error
        row[4] = bsa_se; // Band 4: BSA Standard Error
        row[5] = nadir_se; // Band 5: NBAR Standard Error
        row[6] = standard_error; // Band 6: Regression Model Standard Error (RMSE)
        row[7] = r_squared; // Band 7: Regression R^2
        row[8] = count as f64; // Band 8: Valid observation count (nt)
    }

    output
}

// Verifies that input NumPy ndarrays possess C-contiguous standard memory layout
fn ensure_standard_layout<T>(array: &ndarray::ArrayView2<'_, T>, name: &str) -> PyResult<()> {
    if !array.is_standard_layout() {
        return Err(PyValueError::new_err(format!(
            "{name} must be a C-contiguous array"
        )));
    }
    Ok(())
}

// PyO3-exposed function performing NRT BRDF inversion across an entire raster swath
#[allow(clippy::too_many_arguments)]
#[pyfunction(signature = (y, sz, vz, rz, soz_noon, weighted=true, scale=1.87, progress=true))]
fn nrt_brdf_all<'py>(
    py: Python<'py>,
    y: PyReadonlyArray2<'py, f64>,
    sz: PyReadonlyArray2<'py, f64>,
    vz: PyReadonlyArray2<'py, f64>,
    rz: PyReadonlyArray2<'py, f64>,
    soz_noon: PyReadonlyArray1<'py, f64>,
    weighted: bool,
    scale: f64,
    progress: bool,
) -> PyResult<Bound<'py, numpy::PyArray2<f64>>> {
    // Acquire read-only ndarray views from Python array objects
    let y_view = y.as_array();
    let sz_view = sz.as_array();
    let vz_view = vz.as_array();
    let rz_view = rz.as_array();
    let noon_view = soz_noon.as_array();

    // Ensure all 2D input arrays are C-contiguous
    ensure_standard_layout(&y_view, "y")?;
    ensure_standard_layout(&sz_view, "sz")?;
    ensure_standard_layout(&vz_view, "vz")?;
    ensure_standard_layout(&rz_view, "rz")?;

    // Validate that reflectance and geometric arrays share identical dimensions: (pixel_count, observation_count)
    let dimensions = y_view.dim();
    if sz_view.dim() != dimensions || vz_view.dim() != dimensions || rz_view.dim() != dimensions {
        return Err(PyValueError::new_err(
            "y, sz, vz, and rz must have identical shapes",
        ));
    }

    // Validate that solar noon zenith array length matches pixel row count
    if noon_view.len() != dimensions.0 {
        return Err(PyValueError::new_err(
            "soz_noon length must equal the number of pixel rows",
        ));
    }

    // Validate that at least one observation slice exists
    if dimensions.1 == 0 {
        return Err(PyValueError::new_err(
            "input arrays must contain at least one observation",
        ));
    }

    // Validate temporal decay scale factor
    if !scale.is_finite() || scale <= 0.0 {
        return Err(PyValueError::new_err(
            "scale must be finite and greater than zero",
        ));
    }

    // Acquire direct continuous raw memory slices
    let y_slice = y.as_slice()?;
    let sz_slice = sz.as_slice()?;
    let vz_slice = vz.as_slice()?;
    let rz_slice = rz.as_slice()?;
    let noon_slice = soz_noon.as_slice()?;
    let (pixel_count, observation_count) = dimensions;

    // Allocate continuous container for the accumulated output matrix
    let mut results = Vec::with_capacity(pixel_count * OUTPUT_COLUMNS);

    // Initialize Indicatif progress bar if progress display is requested
    let pb = if progress {
        let bar = ProgressBar::new(pixel_count as u64);
        bar.set_style(
            ProgressStyle::default_bar()
                .template("{spinner:.green} [{elapsed_precise}] [{wide_bar:.cyan/blue}] {pos}/{len} ({percent}%) [ETA: {eta}]")
                .unwrap()
                .progress_chars("#>-"),
        );
        Some(bar)
    } else {
        None
    };

    // Iterate through total raster pixels in bounded chunks
    for start in (0..pixel_count).step_by(PIXEL_CHUNK_SIZE) {
        // Compute number of pixels in this chunk
        let chunk_pixels = PIXEL_CHUNK_SIZE.min(pixel_count - start);
        // Compute starting element offset in 1D array
        let start_observation = start * observation_count;
        // Compute ending element offset in 1D array
        let end_observation = (start + chunk_pixels) * observation_count;

        // Extract chunk buffers
        let y_chunk = y_slice[start_observation..end_observation].to_vec();
        let sz_chunk = sz_slice[start_observation..end_observation].to_vec();
        let vz_chunk = vz_slice[start_observation..end_observation].to_vec();
        let rz_chunk = rz_slice[start_observation..end_observation].to_vec();
        let noon_chunk = noon_slice[start..start + chunk_pixels].to_vec();

        // Release Python Global Interpreter Lock (GIL) during numerical inversion
        let computed = py.allow_threads(|| {
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

        // Advance progress bar by chunk size
        if let Some(ref bar) = pb {
            bar.inc(chunk_pixels as u64);
        }

        // Append chunk outputs to full results array
        results.extend(computed);
    }

    // Finish progress bar display
    if let Some(ref bar) = pb {
        bar.finish_with_message("done");
    }

    // Shape flat results vector into 2D ndarray and convert to Python NumPy array
    let result_array = Array2::from_shape_vec((pixel_count, OUTPUT_COLUMNS), results)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(result_array.into_pyarray(py))
}

// Module declaration for PyO3 Python C-extension
#[pymodule]
fn _brdf_rust(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(nrt_brdf_all, module)?)?;
    Ok(())
}
