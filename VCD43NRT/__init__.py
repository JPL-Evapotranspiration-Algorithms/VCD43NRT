try:
    from . import _brdf_rust
except ImportError as exc:
    raise ImportError(
        "VCD43NRT requires the compiled '_brdf_rust' extension. "
        "Run 'make install' or 'maturin develop --manifest-path rust/Cargo.toml' in a source checkout."
    ) from exc

from .version import __version__, __author__
from .VCD43NRT import VCD43NRT, VCD43NRTGranule, VNP43NRT, VNP43NRTGranule
from .VIIRS import VIIRS
from .VNP09GA import VNP09GA
from .VJ109GA import VJ109GA
from .VJ209GA import VJ209GA
from . import brdf_progress

__all__ = [
    "__version__",
    "__author__",
    "VCD43NRT",
    "VCD43NRTGranule",
    "VNP43NRT",
    "VNP43NRTGranule",
    "VIIRS",
    "VNP09GA",
    "VJ109GA",
    "VJ209GA",
    "brdf_progress",
]
