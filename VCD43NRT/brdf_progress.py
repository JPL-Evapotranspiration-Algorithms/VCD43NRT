"""Progress callback utilities for Rust BRDF solver integration with Python progress bars.

This module provides callback wrappers to connect Rust progress updates with Python
progress bars like tqdm, allowing seamless progress display when calling the Rust
BRDF solver.

Example:
    Using tqdm with the Rust BRDF solver::

        from VCD43NRT import brdf_progress
        from tqdm import tqdm
        import numpy as np

        # Create tqdm progress bar
        pbar = tqdm(total=pixels, desc="BRDF Processing")
        callback = brdf_progress.make_tqdm_callback(pbar)

        # Call Rust function with callback
        results = nrt_brdf_all(
            Y, sz, vz, rz, solar_noon,
            weighted=False,
            progress=True,
            progress_callback=callback
        )

        pbar.close()
"""

from typing import Optional, Callable, Any
import sys


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


class TqdmProgressCallback:
    """Context manager for seamless tqdm integration with Rust BRDF solver.

    This class provides a context manager that automatically creates and cleans up
    a tqdm progress bar for use with the Rust BRDF solver.

    Example:
        >>> from VCD43NRT.brdf_progress import TqdmProgressCallback
        >>> from VCD43NRT import _brdf_rust
        >>> import numpy as np
        >>>
        >>> with TqdmProgressCallback(total_pixels, desc="BRDF") as progress:
        ...     results = _brdf_rust.nrt_brdf_all(
        ...         Y, sz, vz, rz, solar_noon,
        ...         progress=True,
        ...         progress_callback=progress
        ...     )
    """

    def __init__(
        self,
        total: int,
        desc: str = "BRDF Processing",
        unit: str = "pixel",
        **tqdm_kwargs: Any,
    ):
        """Initialize progress context manager.

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
        """Enter context and create tqdm progress bar."""
        try:
            from tqdm import tqdm
        except ImportError:
            raise ImportError(
                "tqdm is required for TqdmProgressCallback. "
                "Install it with: pip install tqdm"
            )

        self.pbar = tqdm(
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
