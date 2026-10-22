import sys
from os.path import abspath, expanduser
from typing import List, Optional, Sequence

import click

from .VIIRS import VIIRS, VIIRSUnavailableError
from .VIIRS_granule import ALBEDO_COLORMAP, NDVI_COLORMAP
from .VIIRS_missions import (
    DEFAULT_MISSION,
    DEFAULT_MISSION_PRIORITY,
    MISSION_ALIASES,
    PREFIX_TO_MISSION,
    VIIRS_FILENAME_REGEX,
    VIIRS_MISSIONS,
    mission_from_filename,
    modland_tile_from_filename,
    normalize_mission,
    normalize_mission_priority,
)
from .VIIRS_query import VIIRS_CMR_query, earliest_datetime, latest_datetime
from .VNP09GA_granule import VNP09GAGranule


class VNP09GA(VIIRS):
    DEFAULT_DOWNLOAD_DIRECTORY = "VNP09GA_download"
    DEFAULT_PRODUCTS_DIRECTORY = "VNP09GA_products"
    DEFAULT_MOSAIC_DIRECTORY = "VNP09GA_mosaics"

    PRODUCT_SHORT_NAME = "VNP09GA"
    DEFAULT_MISSION = "SNPP"
    GRANULE_CLASS = VNP09GAGranule

    def prefetch_VNP09GA(self, start_date, end_date, geometry=None):
        return self.prefetch(start_date=start_date, end_date=end_date, geometry=geometry)


@click.group(name="VNP09GA")
def main():
    """Command line interface for querying and downloading VNP09GA granules."""
    pass


@main.command(name="list")
@click.option("--tile", "-t", required=True, type=str, help="MODIS/VIIRS tile ID (e.g., h08v05).")
@click.option("--start-date", "-s", required=True, type=str, help="Start date (YYYY-MM-DD).")
@click.option("--end-date", "-e", required=True, type=str, help="End date (YYYY-MM-DD).")
@click.option(
    "--mission",
    "-m",
    default=VNP09GA.DEFAULT_MISSION,
    show_default=True,
    help="Target mission (e.g., SNPP, NOAA20, NOAA21).",
)
@click.option(
    "--fallback/--no-fallback",
    default=False,
    help="Enable fallback across prioritized missions if target is unavailable.",
)
def list_granules_command(tile: str, start_date: str, end_date: str, mission: str, fallback: bool):
    """Search and list available VNP09GA granules for a tile across a date range."""
    client = VNP09GA(mission=mission, fallback_mode=fallback)
    granules = client._search_with_fallback(
        start_date=start_date,
        end_date=end_date,
        tile=tile,
    )

    if not granules:
        click.echo(f"No VNP09GA granules found for tile {tile} between {start_date} and {end_date}.")
        return

    click.echo(f"Found {len(granules)} granule(s):")
    for granule in granules:
        filename = client._granule_filename(granule)
        beginning_time = granule["umm"]["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"]
        click.echo(f"  {beginning_time} | {filename}")


@main.command(name="download")
@click.option("--tile", "-t", required=True, type=str, help="MODIS/VIIRS tile ID (e.g., h08v05).")
@click.option("--start-date", "-s", required=True, type=str, help="Start date (YYYY-MM-DD).")
@click.option("--end-date", "-e", required=True, type=str, help="End date (YYYY-MM-DD).")
@click.option(
    "--download-dir",
    "-d",
    default=None,
    type=click.Path(file_okay=False, writable=True),
    help="Directory where downloaded granules will be saved.",
)
@click.option(
    "--mission",
    "-m",
    default=VNP09GA.DEFAULT_MISSION,
    show_default=True,
    help="Target mission (e.g., SNPP, NOAA20, NOAA21).",
)
@click.option(
    "--fallback/--no-fallback",
    default=False,
    help="Enable fallback across prioritized missions if target is unavailable.",
)
def download_granules_command(
    tile: str,
    start_date: str,
    end_date: str,
    download_dir: Optional[str],
    mission: str,
    fallback: bool,
):
    """Download available VNP09GA granules for a tile across a date range."""
    client = VNP09GA(
        download_directory=download_dir,
        mission=mission,
        fallback_mode=fallback,
    )

    granules = client._search_with_fallback(
        start_date=start_date,
        end_date=end_date,
        tile=tile,
    )

    if not granules:
        click.echo(f"No VNP09GA granules found to download for tile {tile} between {start_date} and {end_date}.")
        return

    click.echo(f"Downloading {len(granules)} granule(s) to {client.download_directory}...")
    saved_paths = client.download_granules(granules)
    for path in saved_paths:
        click.echo(f"  {path}")


if __name__ == "__main__":
    main()