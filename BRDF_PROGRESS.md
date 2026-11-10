# Python-Rust BRDF Progress Integration

This document explains how to use progress callbacks with the Rust BRDF solver, allowing flexible progress display in Python via tqdm or custom callbacks.

## Overview

The Rust BRDF solver (`nrt_brdf_all`) now supports two progress display modes:

1. **Indicatif** (default Rust built-in): Terminal progress bar rendered in Rust
2. **Python Callbacks**: Custom progress handling via callable, enabling integration with tqdm or other Python progress libraries

## Quick Start

### Using tqdm (Recommended)

```python
from VCD43NRT import _brdf_rust, brdf_progress
import numpy as np

# Prepare data...
Y, sz, vz, rz, solar_noon = ...

# Create and use progress callback
with brdf_progress.TqdmProgressCallback(total_pixels, desc="Processing BRDF") as progress:
    results = _brdf_rust.nrt_brdf_all(
        Y, sz, vz, rz, solar_noon,
        weighted=False,
        progress=True,
        progress_callback=progress  # Pass callback here
    )
```

### Using Built-in Indicatif

```python
from VCD43NRT import _brdf_rust
import numpy as np

# Prepare data...
Y, sz, vz, rz, solar_noon = ...

# No callback needed - uses default Indicatif bar
results = _brdf_rust.nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    weighted=False,
    progress=True  # Use built-in progress bar
)
```

### No Progress Display

```python
results = _brdf_rust.nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    weighted=False,
    progress=False  # Disable progress display
)
```

## API Reference

### `brdf_progress.TqdmProgressCallback`

Context manager for seamless tqdm integration.

**Usage:**
```python
with TqdmProgressCallback(total=pixels, desc="BRDF", unit="pixel") as callback:
    results = nrt_brdf_all(..., progress_callback=callback)
```

**Parameters:**
- `total` (int): Total number of pixels to process
- `desc` (str): Description for the progress bar (default: "BRDF Processing")
- `unit` (str): Unit name (default: "pixel")
- `**tqdm_kwargs`: Additional keyword arguments passed to tqdm (e.g., `unit_scale=True`, `ncols=80`)

**Returns:** Callback function compatible with `progress_callback` parameter

### `brdf_progress.make_tqdm_callback(pbar)`

Create a progress callback from an existing tqdm progress bar.

**Usage:**
```python
from tqdm import tqdm

pbar = tqdm(total=pixels, desc="Processing")
callback = brdf_progress.make_tqdm_callback(pbar)
results = nrt_brdf_all(..., progress_callback=callback)
pbar.close()
```

**Parameters:**
- `pbar`: Any tqdm progress bar object

**Returns:** Callback function that updates the provided progress bar

### `brdf_progress.make_simple_callback(desc)`

Create a simple text-based progress callback.

**Usage:**
```python
callback = brdf_progress.make_simple_callback("BRDF Processing")
results = nrt_brdf_all(..., progress_callback=callback)
```

**Output:** `BRDF Processing: 50,000 / 100,000 (50.0%)`

**Parameters:**
- `desc` (str): Description prefix for output (default: "Processing")

**Returns:** Callback function that prints to stdout

### `brdf_progress.make_custom_callback(update_func)`

Wrap a custom update function as a progress callback.

**Usage:**
```python
def my_progress(current, total):
    percent = 100.0 * current / total
    print(f"Progress: {percent:.1f}%")

callback = brdf_progress.make_custom_callback(my_progress)
results = nrt_brdf_all(..., progress_callback=callback)
```

**Parameters:**
- `update_func`: Callable with signature `(current: int, total: int) -> None`

**Returns:** The same callback function

## Callback Signature

All progress callbacks have the same signature:

```python
def callback(current: int, total: int) -> None:
    """Update progress display.
    
    Args:
        current: Number of pixels processed so far
        total: Total number of pixels to process
    """
    pass
```

## Rust Function Signature

The updated Rust BRDF function now has this signature:

```python
def nrt_brdf_all(
    y: ndarray,                           # Reflectance observations
    sz: ndarray,                          # Solar zenith angles
    vz: ndarray,                          # Sensor/view zenith angles
    rz: ndarray,                          # Relative azimuth angles
    soz_noon: ndarray,                    # Solar noon zenith angles
    weighted: bool = True,                # Use weighted least squares
    scale: float = 1.87,                  # BRDF model scale factor
    progress: bool = True,                # Show progress
    progress_callback: Optional[Callable] = None  # Custom callback
) -> ndarray:                             # Results array
    """Compute BRDF-adjusted reflectance for all pixels."""
```

## Examples

### Example 1: Using tqdm with custom formatting

```python
from VCD43NRT import _brdf_rust, brdf_progress
from tqdm import tqdm
import numpy as np

pixels = 250_000
# ... prepare Y, sz, vz, rz, solar_noon ...

with tqdm(total=pixels, desc="BRDF Inversion", unit=" pixels", 
          unit_scale=True, unit_divisor=1000) as pbar:
    callback = brdf_progress.make_tqdm_callback(pbar)
    results = _brdf_rust.nrt_brdf_all(
        Y, sz, vz, rz, solar_noon,
        weighted=False,
        progress=True,
        progress_callback=callback
    )
```

### Example 2: Custom progress tracking

```python
from VCD43NRT import _brdf_rust, brdf_progress
import numpy as np

class CustomProgress:
    def __init__(self, total):
        self.total = total
        self.current = 0
    
    def __call__(self, current, total):
        self.current = current
        percent = 100.0 * current / total
        print(f"[{'#' * int(percent/2)}{' ' * (50-int(percent/2))}] {percent:.1f}%")

# ... prepare data ...
progress = CustomProgress(pixels)
results = _brdf_rust.nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    progress=True,
    progress_callback=progress
)
```

### Example 3: Integration with VCD43NRT workflows

```python
from VCD43NRT import VCD43NRT, brdf_progress

# In your processing code:
with brdf_progress.TqdmProgressCallback(total_pixels) as progress:
    # The VCD43NRT function would need to be updated to pass callback
    results = vcd_processor.compute_brdf(
        ...,
        progress_callback=progress
    )
```

## Implementation Notes

### Progress Callback Behavior

- Callbacks are called **after each chunk** (4096 pixels by default) is processed
- The callback receives `(processed_pixels, total_pixels)` as arguments
- Callbacks run in the Python thread, allowing GIL-based operations
- The callback function must complete quickly to avoid slowing down processing

### Performance Considerations

- **No callback** (`progress=False`): Fastest option, no overhead
- **Simple callback**: Minimal overhead, fast text I/O
- **tqdm callback**: Slightly more overhead due to terminal I/O, but handles many pixels efficiently
- **Indicatif** (default): Built-in Rust progress bar, comparable to simple callback

### Thread Safety

- Progress callbacks are called from the main Python thread
- The callback **must be thread-safe** if you use threading elsewhere
- Rust processing releases the GIL, so callbacks can use Python libraries safely

## Testing

Run the provided test suite:

```bash
# Test all progress modes
python examples/brdf_progress_examples.py --pixels 100000

# Test specific mode
python examples/brdf_progress_examples.py --test tqdm --pixels 50000
python examples/brdf_progress_examples.py --test indicatif --pixels 50000
python examples/brdf_progress_examples.py --test simple --pixels 50000
python examples/brdf_progress_examples.py --test none --pixels 50000
```

## Troubleshooting

**Issue**: Callback doesn't seem to be called
- Ensure `progress=True` is set
- Ensure callback is not None
- Check if total pixels is greater than chunk size (4096)

**Issue**: tqdm bar looks strange or corrupted
- This is normal when mixing print statements with tqdm
- Use tqdm with `position=0` and `leave=True` for best results
- Consider using `contextlib.redirect_stdout()` for cleaner output

**Issue**: Indicatif progress bar not showing
- Indicatif may suppress output in some environments
- Use a callback instead for better compatibility

## References

- [PyO3 Documentation](https://pyo3.rs/)
- [tqdm Documentation](https://tqdm.github.io/)
- [Indicatif Documentation](https://docs.rs/indicatif/latest/indicatif/)
