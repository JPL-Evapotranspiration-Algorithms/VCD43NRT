# Jupyter Notebook Support for BRDF Progress

## Overview

The BRDF progress system now has **first-class Jupyter notebook support** with automatic environment detection. The default progress display is now **tqdm** which works beautifully in both terminal and Jupyter environments.

## Key Features

✨ **Automatic Environment Detection**
- Detects Jupyter vs. Terminal automatically
- Uses `tqdm.notebook.tqdm` in Jupyter (rendered as HTML widget)
- Uses `tqdm.tqdm` in terminal (rendered as text)
- Same code works everywhere

✨ **tqdm is Now the Default**
- No need to manually specify progress callback
- Just call the function, get progress bar automatically
- Beautiful, professional appearance in all environments

✨ **100% Backward Compatible**
- Existing code continues to work
- Can still disable progress or use custom callbacks
- Drop-in replacement for old API

## Quick Start

### Jupyter Notebook (Automatic)

```python
from VCD43NRT.brdf_progress import nrt_brdf_all
import numpy as np

# Prepare data...
Y, sz, vz, rz, solar_noon = ...

# That's it! Progress bar works automatically in Jupyter
results = nrt_brdf_all(Y, sz, vz, rz, solar_noon)
```

The progress bar will appear as a beautiful HTML widget in your Jupyter notebook.

### Terminal (Automatic)

```python
from VCD43NRT.brdf_progress import nrt_brdf_all
import numpy as np

# Same code works in terminal!
results = nrt_brdf_all(Y, sz, vz, rz, solar_noon)
```

The progress bar appears as a text bar in your terminal.

### JupyterLab, Google Colab, etc.

```python
# Works exactly the same in all environments
results = nrt_brdf_all(Y, sz, vz, rz, solar_noon)
```

## API Reference

### Smart Progress Wrapper

```python
def nrt_brdf_all(
    y, sz, vz, rz, soz_noon,
    weighted: bool = True,
    scale: float = 1.87,
    progress: bool = True,
    progress_callback: Optional[Callable] = "auto",
    desc: str = "BRDF Inversion",
    **tqdm_kwargs
)
```

**Parameters:**
- `progress_callback: "auto"` (default) - Use smart tqdm with Jupyter detection
- `progress_callback: None` - Disable progress display
- `progress_callback: callable` - Use custom callback function
- `desc` - Description for progress bar (default: "BRDF Inversion")
- `**tqdm_kwargs` - Additional tqdm options (e.g., `unit_scale=True`)

### Get Smart Callback

```python
from VCD43NRT.brdf_progress import get_smart_callback

callback = get_smart_callback(
    total=pixels,
    desc="Processing",
    unit_scale=True,  # Show MB/s instead of pixels/s
    ncols=80          # Width of progress bar
)
```

### Smart TqdmProgressCallback

```python
from VCD43NRT.brdf_progress import TqdmProgressCallback
from VCD43NRT import _brdf_rust

with TqdmProgressCallback(total=pixels, desc="BRDF") as progress:
    results = _brdf_rust.nrt_brdf_all(
        Y, sz, vz, rz, solar_noon,
        progress=True,
        progress_callback=progress
    )
```

Automatically uses `tqdm.notebook.tqdm` in Jupyter or `tqdm.tqdm` in terminal.

### Detect Notebook Environment

```python
from VCD43NRT.brdf_progress import is_notebook

if is_notebook():
    print("Running in Jupyter notebook")
else:
    print("Running in terminal")
```

## Usage Examples

### Example 1: Simplest (Jupyter or Terminal)

```python
from VCD43NRT.brdf_progress import nrt_brdf_all

# Just call it - progress bar appears automatically!
results = nrt_brdf_all(Y, sz, vz, rz, solar_noon)
```

### Example 2: With Custom Description

```python
results = nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    desc="Processing VIIRS Granule",  # Custom description
)
```

### Example 3: With Unit Scaling (shows MB/s, etc.)

```python
results = nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    unit_scale=True,      # Show MB/s instead of pixels/s
    unit_divisor=1000,    # Use 1000 instead of 1024 for MB
)
```

### Example 4: Disable Progress

```python
results = nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    progress=False  # No progress bar
)
```

### Example 5: Custom Callback

```python
def my_progress(current, total):
    percent = 100.0 * current / total
    print(f"Progress: {percent:.1f}%")

results = nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    progress_callback=my_progress
)
```

### Example 6: Classic Context Manager (Jupyter-friendly)

```python
from VCD43NRT.brdf_progress import TqdmProgressCallback
from VCD43NRT import _brdf_rust

with TqdmProgressCallback(total=pixels, desc="BRDF") as progress:
    results = _brdf_rust.nrt_brdf_all(
        Y, sz, vz, rz, solar_noon,
        progress=True,
        progress_callback=progress
    )
```

## Environment-Specific Behavior

### Terminal Output
```
BRDF Inversion:  45%|████▌     | 4500/10000 [00:02<00:02, 2100 pixel/s]
```

### Jupyter Output
Renders as an interactive HTML widget with:
- Animated progress bar
- Percentage display
- Speed (pixels/s)
- Estimated time remaining
- Total time elapsed

### Google Colab
Works automatically with Colab's native progress widget integration.

### JupyterLab
Works with native notebook progress bars.

## Advanced Configuration

### Custom tqdm Options

```python
from VCD43NRT.brdf_progress import nrt_brdf_all

results = nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    desc="BRDF Processing",
    unit_scale=True,           # Show MB/s
    unit_divisor=1000,         # Use 1000 for MB (not 1024)
    ncols=100,                 # Bar width
    ascii=True,                # ASCII only (no Unicode)
    position=0,                # Bar position (for multiple bars)
    leave=True,                # Keep bar after completion
)
```

### Multiple Progress Bars (Expert)

```python
from VCD43NRT.brdf_progress import TqdmProgressCallback
from VCD43NRT import _brdf_rust
from tqdm.notebook import tqdm  # Use notebook tqdm

# Outer progress bar
with tqdm(total=num_granules, desc="Granules", position=0) as outer_pbar:
    for granule in granules:
        # Inner progress bar for BRDF processing
        with TqdmProgressCallback(total=pixels, desc="BRDF", position=1) as progress:
            results = _brdf_rust.nrt_brdf_all(
                Y, sz, vz, rz, solar_noon,
                progress=True,
                progress_callback=progress
            )
        outer_pbar.update(1)
```

## Troubleshooting

### Progress bar doesn't appear in Jupyter

**Solution 1: Restart kernel**
```python
# Restart Jupyter kernel (Kernel > Restart)
from IPython import display
display.clear_output()
```

**Solution 2: Install tqdm for Jupyter**
```bash
pip install "tqdm[jupyter]"
# or
conda install tqdm
```

**Solution 3: Use the notebook backend explicitly**
```python
from tqdm.notebook import tqdm
# Now use tqdm as normal
```

### Progress bar looks broken or overlapping

**Solution:** Set position parameter
```python
results = nrt_brdf_all(
    Y, sz, vz, rz, solar_noon,
    position=0,  # Force position
    ncols=100,   # Set width
)
```

### Function detects terminal when running in Jupyter

**Solution:** Ensure Jupyter is properly configured
```python
from IPython import get_ipython
print(get_ipython())  # Should print an instance, not None
```

### tqdm import error

**Solution:** Install tqdm with Jupyter support
```bash
pip install tqdm[jupyter]
```

## Performance

- **No overhead** when `progress=False`
- **~1ms per update** (negligible for long operations)
- **Jupyter rendering** is optimized for smooth updates
- **Automatic batching** reduces terminal refresh rate

## Migration Guide

### From Indicatif to tqdm (Default)

Old code:
```python
from VCD43NRT import _brdf_rust
results = _brdf_rust.nrt_brdf_all(..., progress=True)
```

New code (same or simpler):
```python
from VCD43NRT.brdf_progress import nrt_brdf_all
results = nrt_brdf_all(...)  # tqdm is default!
```

### From Manual tqdm Setup

Old code:
```python
from tqdm import tqdm
from VCD43NRT import _brdf_rust

pbar = tqdm(total=pixels)
callback = brdf_progress.make_tqdm_callback(pbar)
results = _brdf_rust.nrt_brdf_all(..., progress_callback=callback)
pbar.close()
```

New code (cleaner):
```python
from VCD43NRT.brdf_progress import nrt_brdf_all
results = nrt_brdf_all(...)  # Auto-handles tqdm!
```

## FAQ

**Q: Will my existing code break?**
A: No! All existing code continues to work. The new functions are additions.

**Q: Does it work in Google Colab?**
A: Yes! Google Colab automatically uses the notebook version of tqdm.

**Q: Can I use this with Dask or parallel processing?**
A: Yes, but use custom callbacks for thread-safe progress tracking.

**Q: How do I hide the progress bar?**
A: Use `progress=False` or `progress_callback=None`

**Q: Does it work with dark/light notebook themes?**
A: Yes! tqdm automatically adjusts to the notebook theme.

**Q: Can I customize the progress bar appearance?**
A: Yes! Use tqdm options like `ncols`, `bar_format`, `colour`, etc.

## See Also

- [PROGRESS_QUICKREF.md](PROGRESS_QUICKREF.md) - Quick reference for all progress modes
- [BRDF_PROGRESS.md](BRDF_PROGRESS.md) - Complete API documentation
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical implementation details
- [tqdm Documentation](https://tqdm.github.io/) - More tqdm options
