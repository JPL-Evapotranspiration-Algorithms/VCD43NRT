class VCD43NRTError(Exception):
    """Base exception for all VCD43NRT domain errors."""


class CMRServerUnreachable(VCD43NRTError):
    """Remote NASA CMR or Earthdata authentication service cannot be reached."""


class DownloadFailed(VCD43NRTError, ConnectionError):
    """Granule or ancillary transfer failed after retries, or downloaded file is unreadable."""


class AuxiliaryDownloadFailed(DownloadFailed):
    """Required ancillary meteorological/aerosol input (e.g., GEOS-5 FP AOT) failed to download."""


class VIIRSUnavailableError(VCD43NRTError):
    """CMR query succeeded, but no VIIRS granule exists for the requested tile, date, and mission(s)."""


class BRDFRetrievalFailed(VCD43NRTError, RuntimeError):
    """BRDF kernel inversion could not be completed (e.g., <7 valid observations or singular fit)."""


__all__ = [
    "VCD43NRTError",
    "CMRServerUnreachable",
    "DownloadFailed",
    "AuxiliaryDownloadFailed",
    "VIIRSUnavailableError",
    "BRDFRetrievalFailed",
]
