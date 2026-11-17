import logging
import random
import time
from datetime import date, datetime
from typing import List, Union

from dateutil import parser
import earthaccess
from rasters import Point, Polygon, RasterGeometry
import colored_logging as cl

from .constants import CMR_BACKOFF_SECONDS, CMR_RETRIES
from .VIIRS_missions import VIIRS_MISSIONS, normalize_mission

try:
    from .exceptions import CMRServerUnreachable
except ImportError:  # pragma: no cover - legacy direct-import fallback
    from exceptions import CMRServerUnreachable


logger = logging.getLogger(__name__)


def earliest_datetime(date_in: Union[date, str]) -> datetime:
    if isinstance(date_in, str):
        datetime_in = parser.parse(date_in)
    else:
        datetime_in = date_in

    date_string = datetime_in.strftime("%Y-%m-%d")
    return parser.parse(f"{date_string}T00:00:00Z")


def latest_datetime(date_in: Union[date, str]) -> datetime:
    if isinstance(date_in, str):
        datetime_in = parser.parse(date_in)
    else:
        datetime_in = date_in

    date_string = datetime_in.strftime("%Y-%m-%d")
    return parser.parse(f"{date_string}T23:59:59Z")


def VIIRS_CMR_query(
    start_date: Union[date, str],
    end_date: Union[date, str],
    mission: str,
    target_geometry: Point or Polygon or RasterGeometry = None,
    tile: str = None,
    retries: int = CMR_RETRIES,
    backoff_seconds: float = CMR_BACKOFF_SECONDS,
) -> List[earthaccess.search.DataGranule]:
    mission_key = normalize_mission(mission)
    mission_config = VIIRS_MISSIONS[mission_key]

    query = earthaccess.granule_query().temporal(earliest_datetime(start_date), latest_datetime(end_date))

    concept_id = mission_config.concept_id
    if concept_id:
        query = query.concept_id(concept_id)
    else:
        query = query.short_name(mission_config.short_name)

    if isinstance(target_geometry, Point):
        query = query.point(target_geometry.x, target_geometry.y)
    if isinstance(target_geometry, Polygon):
        ring = target_geometry.exterior
        if not ring.is_ccw:
            ring = ring.reverse()
        query = query.polygon(ring.coords)
    if isinstance(target_geometry, RasterGeometry):
        ring = target_geometry.corner_polygon_latlon.exterior
        if not ring.is_ccw:
            ring = ring.reverse()
        query = query.polygon(ring.coords)
    if tile is not None:
        query = query.readable_granule_name(f"*.{tile}.*")

    max_attempts = max(1, int(retries))
    last_error = None
    granules = None

    for attempt in range(1, max_attempts + 1):
        try:
            granules = query.get()
            break
        except Exception as e:
            last_error = e
            if attempt >= max_attempts:
                raise CMRServerUnreachable(e) from e
            delay = backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, 1.0)
            logger.warning(
                f"CMR query failed for {mission_config.display_name} "
                f"(attempt {attempt}/{max_attempts}): {e}; retrying in {delay:.1f}s"
            )
            time.sleep(delay)

    granules = sorted(
        granules,
        key=lambda granule: granule["umm"]["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"],
    )

    logger.info(f"Found the following granules for {mission_config.display_name} using the CMR search:")
    for granule in granules:
        logger.info("  " + cl.file(granule["meta"]["native-id"]))
    logger.info(f"Number of {mission_config.display_name} granules found using CMR search: {len(granules)}")

    return granules
