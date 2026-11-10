"""Progress callback utilities for Rust BRDF solver integration with Python progress bars.

This module provides callback wrappers to connect Rust progress updates with Python
progress bars like tqdm, allowing seamless progress display when calling the Rust
BRDF solver. tqdm is the default progress display, with automatic Jupyter notebook
detection.

Example:
    Using tqdm with the Rust BRDF solver (automatic in terminals and Jupyter)::

        from VCD43NRT import brdf_progress
        from VCD43NRT import _brdf_rust
        import numpy as np

        # tqdm is automatically used with Jupyter detection
        results = _brdf_rust.nrt_brdf_all(
            Y, sz, vz, rz, solar_noon,
            weighted=False,
            progress=True  # Uses tqdm by default!
        )

    Or explicitly create a progress bar::

        callback = brdf_progress.get_smart_callback(pixels, desc="BRDF Processing")
        results = _brdf_rust.nrt_brdf_all(
            Y, sz, vz, rz, solar_noon,
            progress=True,
            progress_callback=callback
        )
"""

from typing import Optional, Callable, Any
import sys


def is_notebook() -> bool:
    """Detect if code is running in Jupyter notebook or similar environment.
    
    Returns:
        True if running in Jupyter/IPython notebook, False otherwise
    """
    try:
        # Check for IPython
        from IPython import get_ipython
        ipython = get_ipython()
        if ipython is None:
            return False
        # Check if IPython kernel is running in Jupyter
        if "IPKernelApp" not in ipython.config:
            return False
        return True
    except (ImportError, AttributeError, NameError):
        return False


def get_tqdm_class():
    """Get appropriate tqdm class for current environment (terminal or notebook).
    
    Returns:
        tqdm class: tqdm.notebook.tqdm for Jupyter, tqdm.tqdm for terminal
    """
    try:
        if is_notebook():
            from tqdm.notebook import tqdm
            return tqdm
        else:
            from tqdm import tqdm
            return tqdm
    except ImportError:
        raise ImportError(
            "tqdm is required for progress display. "
            "Install it with: pip install tqdm"
        )


def make_tqdm_callback(pbar: Any) -> Callable[[int, int], None]:
    """Create a progress callback for tqdm progress bar.

    Args:
        pbar: A tqdm progress bar object (any tqdm variant)

    Returns:
        A callback function compatible with Rust progress_callback parameter.
        The callback accepts (current: int, total: int) and updates the tqdm bar.

    Example:
        >>> from tqdm import tqdm
        >>> pbar = tqdm(total=1000)
        >>> callback = make_tqdm_callback(pbar)
        >>> callback(500, 1000)  # Updates bar to 50%
        >>> pbar.close()
    """
    last_update = [0]  # Track last update position to batch updates

    def callback(current: int, total: int) -> None:
        """Update tqdm progress bar with current progress.

        Args:
            current: Number of pixels processed so far
            total: Total number of pixels to process
        """
        # Update by delta from last position to avoid excessive updates
        delta = current - last_update[0]
        if delta > 0:
            pbar.update(delta)
            last_update[0] = current

    return callback


def make_simple_callback(desc: str = "Processing") -> Callable[[int, int], None]:
    """Create a simple text-based progress callback for terminal output.

    Args:
        desc: Description prefix for progress output (default: "Processing")

    Returns:
        A callback function that prints progress to stdout

    Example:
        >>> callback = make_simple_callback("BRDF")
        >>> callback(500, 1000)
        BRDF: 500 / 1000 (50.0%)
    """

    def callback(current: int, total: int) -> None:
        """Print simple progress to stdout.

        Args:
            current: Number of pixels processed so far
            total: Total number of pixels to process
        """
        percent = 100.0 * current / total if total > 0 else 0
        print(f"\r{desc}: {current:,} / {total:,} ({percent:.1f}%)", end="", flush=True)

    # Print newline at the end if callback exists
    import atexit

    def finish() -> None:
        print()  # Move to next line when done

    atexit.register(finish)

    return callback


def make_custom_callback(
    update_func: Callable[[int, int], None],
) -> Callable[[int, int], None]:
    """Create a custom progress callback from a user-provided function.

    Args:
        update_func: Function with signature (current: int, total: int) -> None
                     that handles progress updates

    Returns:
        A callback function compatible with Rust progress_callback parameter

    Example:
        >>> def my_progress(current, total):
        ...     print(f"Progress: {current}/{total}")
        >>> callback = make_custom_callback(my_progress)
    """
    return update_func


def get_smart_callback(
    total: int,
    desc: str = "Processing",
    unit: str = "pixel",
    **tqdm_kwargs: Any,
) -> Callable[[int, int], None]:
    """Create a smart tqdm progress callback with automatic environment detection.

    Automatically detects whether running in terminal or Jupyter notebook and
    uses the appropriate tqdm variant (tqdm.notebook.tqdm for Jupyter, tqdm.tqdm
    for terminal).

    Args:
        total: Total number of items to process
        desc: Description for the progress bar (default: "Processing")
        unit: Unit name for progress bar (default: "pixel")
        **tqdm_kwargs: Additional keyword arguments passed to tqdm

    Returns:
        A callback function compatible with Rust progress_callback parameter

    Example:
        >>> callback = get_smart_callback(total=1000, desc="BRDF")
        >>> # Automatically uses tqdm.notebook.tqdm in Jupyter
        >>> # Automatically uses tqdm.tqdm in terminal
    """
    try:
        tqdm_class = get_tqdm_class()
    except ImportError as e:
        raise ImportError(
            "tqdm is required for progress display. "
            "Install it with: pip install tqdm"
        ) from e

    pbar = tqdm_class(
        total=total,
        desc=desc,
        unit=unit,
        **tqdm_kwargs,
    )

    return make_tqdm_callback(pbar)


class TqdmProgressCallback:
    """Context manager for seamless tqdm integration with Rust BRDF solver.

    This class provides a context manager that automatically creates and cleans up
    a tqdm progress bar for use with the Rust BRDF solver. Automatically detects
    Jupyter notebook environment and uses appropriate tqdm variant.

    Example (Terminal):
        >>> from VCD43NRT.brdf_progress import TqdmProgressCallback
        >>> from VCD43NRT import _brdf_rust
        >>>
        >>> with TqdmProgressCallback(total_pixels, desc="BRDF") as progress:
        ...     results = _brdf_rust.nrt_brdf_all(
        ...         Y, sz, vz, rz, solar_noon,
        ...         progress=True,
        ...         progress_callback=progress
        ...     )

    Example (Jupyter):
        >>> # In Jupyter notebook - use exactly the same code!
        >>> # tqdm.notebook.tqdm is automatically used
    """

    def __init__(
        self,
        total: int,
        desc: str = "BRDF Processing",
        unit: str = "pixel",
        **tqdm_kwargs: Any,
    ):
        """Initialize progress context manager with smart environment detection.

        Args:
            total: Total number of pixels to process
            desc: Description for the progress bar
            unit: Unit name for progress bar (default: "pixel")
            **tqdm_kwargs: Additional keyword arguments passed to tqdm
        """
        self.total = total
        self.desc = desc
        self.unit = unit
        self.tqdm_kwargs = tqdm_kwargs
        self.pbar: Optional[Any] = None
        self.callback: Optional[Callable[[int, int], None]] = None

    def __enter__(self) -> Callable[[int, int], None]:
        """Enter context and create tqdm progress bar with Jupyter detection."""
        try:
            tqdm_class = get_tqdm_class()
        except ImportError as e:
            raise ImportError(
                "tqdm is required for TqdmProgressCallback. "
                "Install it with: pip install tqdm"
            ) from e

        self.pbar = tqdm_class(
            total=self.total,
            desc=self.desc,
            unit=self.unit,
            **self.tqdm_kwargs,
        )
        self.callback = make_tqdm_callback(self.pbar)
        return self.callback

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context and close progress bar."""
        if self.pbar is not None:
            self.pbar.close()


def no_progress() -> Optional[Callable[[int, int], None]]:
    """Return None callback for no progress display.

    This is a convenience function to explicitly request no progress callback.

    Returns:
        None, indicating no progress callback should be used
    """
    return None


def nrt_brdf_all(
    y,
    sz,
    vz,
    rz,
    soz_noon,
    weighted: bool = True,
    scale: float = 1.87,
    progress: bool = True,
    progress_callback: Optional[Callable[[int, int], None]] = "auto",
    desc: str = "BRDF Inversion",
    **tqdm_kwargs: Any,
):
    """Wrapper around Rust nrt_brdf_all with smart tqdm default.

    This function wraps the Rust BRDF solver and automatically uses tqdm progress
    bars by default (with Jupyter notebook detection). It provides a convenient
    interface that:
    - Uses tqdm by default (not Indicatif)
    - Auto-detects Jupyter notebooks
    - Allows disabling progress or using custom callbacks
    - Preserves all Rust function parameters

    Args:
        y, sz, vz, rz, soz_noon: Input arrays (see Rust function)
        weighted: Use weighted least squares (default: True)
        scale: BRDF model scale factor (default: 1.87)
        progress: Enable progress display (default: True)
        progress_callback: Progress callback function or "auto" for default tqdm
                          - "auto": Use smart tqdm with Jupyter detection
                          - None: No progress display
                          - callable: Custom callback function
        desc: Description for tqdm progress bar (default: "BRDF Inversion")
        **tqdm_kwargs: Additional arguments passed to tqdm (e.g., unit_scale=True)

    Returns:
        Results array from Rust solver

    Example (Terminal - automatic tqdm):
        >>> from VCD43NRT.brdf_progress import nrt_brdf_all
        >>> results = nrt_brdf_all(Y, sz, vz, rz, solar_noon)

    Example (Jupyter - automatic tqdm.notebook.tqdm):
        >>> # Same code works in Jupyter - uses notebook progress bar automatically!
        >>> results = nrt_brdf_all(Y, sz, vz, rz, solar_noon)

    Example (Disable progress):
        >>> results = nrt_brdf_all(Y, sz, vz, rz, solar_noon, progress=False)

    Example (Custom callback):
        >>> def my_callback(current, total):
        ...     print(f"{current}/{total}")
        >>> results = nrt_brdf_all(Y, sz, vz, rz, solar_noon,
        ...                        progress_callback=my_callback)
    """
    from VCD43NRT import _brdf_rust

    # Handle progress callback logic
    if not progress:
        # Progress disabled
        final_callback = None
    elif progress_callback == "auto":
        # Auto mode: Use smart tqdm with Jupyter detection
        if progress:
            final_callback = get_smart_callback(
                total=y.shape[0],
                desc=desc,
                **tqdm_kwargs
            )
        else:
            final_callback = None
    elif progress_callback is None:
        # Explicitly no callback
        final_callback = None
    else:
        # Custom callback provided
        final_callback = progress_callback

    # Call the Rust function with the determined callback
    return _brdf_rust.nrt_brdf_all(
        y,
        sz,
        vz,
        rz,
        soz_noon,
        weighted=weighted,
        scale=scale,
        progress=progress,
        progress_callback=final_callback,
    )
