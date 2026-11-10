# Quick Reference: Python-Rust Progress Bar Integration

## Three Ways to Show Progress

### 1️⃣ Built-in Rust Progress (Default)
```python
results = _brdf_rust.nrt_brdf_all(Y, sz, vz, rz, solar_noon, progress=True)
```
✅ Simple, works out-of-box
❌ Limited customization

### 2️⃣ Python tqdm (Recommended)
```python
from VCD43NRT import brdf_progress

with brdf_progress.TqdmProgressCallback(total=pixels) as progress:
    results = _brdf_rust.nrt_brdf_all(Y, sz, vz, rz, solar_noon, 
                                      progress=True, progress_callback=progress)
```
✅ Beautiful, professional look
✅ Customizable with tqdm options
✅ Unit scaling, ETA display

### 3️⃣ Simple Text Progress
```python
from VCD43NRT import brdf_progress

callback = brdf_progress.make_simple_callback("Processing")
results = _brdf_rust.nrt_brdf_all(Y, sz, vz, rz, solar_noon, 
                                  progress=True, progress_callback=callback)
```
✅ Lightweight, no dependencies
✅ Works everywhere

### 4️⃣ No Progress
```python
results = _brdf_rust.nrt_brdf_all(Y, sz, vz, rz, solar_noon, progress=False)
```
✅ Fastest, no overhead

## Common Patterns

### With Custom Formatting
```python
from tqdm import tqdm
from VCD43NRT import brdf_progress

pbar = tqdm(total=pixels, unit=" pixels", unit_scale=True, unit_divisor=1000)
callback = brdf_progress.make_tqdm_callback(pbar)
results = _brdf_rust.nrt_brdf_all(..., progress_callback=callback)
pbar.close()
```

### With Custom Callback
```python
def my_progress(current, total):
    percent = 100.0 * current / total
    print(f"Progress: {percent:.1f}%")

callback = brdf_progress.make_custom_callback(my_progress)
results = _brdf_rust.nrt_brdf_all(..., progress_callback=callback)
```

### Multiple Callbacks
```python
def combined(current, total):
    # Log to file
    with open('progress.log', 'a') as f:
        f.write(f"{current}/{total}\n")
    # Also print
    print(f"Processed {current:,} pixels")

results = _brdf_rust.nrt_brdf_all(..., progress_callback=combined)
```

## Function Signatures

### `nrt_brdf_all(...)`
New parameter (all others unchanged):
```python
progress_callback: Optional[Callable[[int, int], None]] = None
```
- `current` (int): Pixels processed so far
- `total` (int): Total pixels to process

### Context Manager
```python
with TqdmProgressCallback(total=pixels, desc="Task", unit="pixel") as cb:
    # cb is a valid progress_callback
    results = nrt_brdf_all(..., progress_callback=cb)
```

### Factories
```python
# From existing tqdm
cb = make_tqdm_callback(tqdm_object)

# Simple text output
cb = make_simple_callback("BRDF")

# Wrap any function
cb = make_custom_callback(lambda c, t: print(f"{c}/{t}"))
```

## Performance

| Mode        | Overhead  | Best For            |
|-------------|-----------|---------------------|
| No progress | None      | Batch processing    |
| Indicatif   | Minimal   | Interactive CLI     |
| tqdm        | ~1ms/call | User scripts        |
| Simple text | ~0.5ms    | Logging, servers    |

Updates occur every ~4096 pixels (chunk size).

## Backward Compatibility

✅ All existing code works unchanged:
```python
# Old code - still works, uses Indicatif
results = nrt_brdf_all(Y, sz, vz, rz, solar_noon)

# New code - choose your progress display
results = nrt_brdf_all(..., progress_callback=my_callback)
```

## Environment Support

| Feature    | Terminal | Jupyter | Subprocess | Notes         |
|------------|----------|---------|------------|---------------|
| Indicatif  | ✅       | ⚠️      | ❌        | May suppress  |
| tqdm       | ✅       | ✅      | ⚠️        | Needs capture |
| Simple     | ✅       | ✅      | ✅        | Plain text    |
| Custom     | ✅       | ✅      | ✅        | User defined  |

## Troubleshooting

**Q: Progress bar looks weird?**
→ Use `position=0, leave=True` in tqdm: 
```python
TqdmProgressCallback(total=pixels, position=0, leave=True)
```

**Q: No callback output?**
→ Ensure `progress=True` and callback is not None

**Q: Very slow with callbacks?**
→ Callback runs in Python thread, possible performance impact
→ Try simple callback instead of tqdm

**Q: Want to cancel mid-processing?**
→ Not yet supported; set callback as None for next version

## Files

📄 **Documentation**
- `BRDF_PROGRESS.md` - Full API documentation
- `ARCHITECTURE.md` - Technical implementation details
- This file - Quick reference

📦 **Code**
- `VCD43NRT/brdf_progress.py` - Progress utilities
- `rust/src/lib.rs` - Rust implementation
- `examples/brdf_progress_examples.py` - Usage examples

✅ **Tests**
- `tests/test_brdf_progress.py` - Comprehensive test suite

## Examples

```bash
# Run all progress modes
python examples/brdf_progress_examples.py --pixels 100000

# Run specific mode
python examples/brdf_progress_examples.py --test tqdm --pixels 50000

# Run tests
python -m pytest tests/test_brdf_progress.py -v
```

## Next Steps

1. ✅ Install latest VCD43NRT (with progress support)
2. ✅ Choose your progress display method
3. ✅ Update your code to use `progress_callback` parameter
4. ✅ Enjoy beautiful progress bars!

```python
from VCD43NRT import _brdf_rust, brdf_progress

# That's it!
with brdf_progress.TqdmProgressCallback(total=1_000_000) as progress:
    results = _brdf_rust.nrt_brdf_all(..., progress_callback=progress)
```
