import sys
from os.path import abspath, expanduser
from typing import List, Optional, Sequence

import click

from .VIIRS import VIIRS, VIIRSUnavailableError
from .VJ109GA_granule import VJ109GAGranule


class VJ109GA(VIIRS):
    DEFAULT_DOWNLOAD_DIRECTORY = "VJ109GA_download"
    DEFAULT_PRODUCTS_DIRECTORY = "VJ109GA_products"
    DEFAULT_MOSAIC_DIRECTORY = "VJ109GA_mosaics"

    PRODUCT_SHORT_NAME = "VJ109GA"
    DEFAULT_MISSION = "NOAA20"
    GRANULE_CLASS = VJ109GAGranule

    def prefetch_VJ109GA(self, start_date, end_date, geometry=None):
        return self.prefetch(start_date=start_date, end_date=end_date, geometry=geometry)


@click.group(name="VJ109GA")
def main():
    """Command line interface for querying and downloading VJ109GA granules."""
    pass


@main.command(name="list")
@click.option("--tile", "-t", required=True, type=str, help="MODIS/VIIRS tile ID (e.g., h08v05).")
@click.option("--start-date", "-s", required=True, type=str, help="Start date (YYYY-MM-DD).")
@click.option("--end-date", "-e", required=True, type=str, help="End date (YYYY-MM-DD).")
@click.option(
    "--mission",
    "-m",
    default=VJ109GA.DEFAULT_MISSION,
    show_default=True,
    help="Target mission (e.g., NOAA20, SNPP, NOAA21).",
)
@click.option(
    "--fallback/--no-fallback",
    default=False,
    help="Enable fallback across prioritized missions if target is unavailable.",
)
def list_granules_command(tile: str, start_date: str, end_date: str, mission: str, fallback: bool):
    """Search and list available VJ109GA granules for a tile across a date range."""
    client = VJ109GA(mission=mission, fallback_mode=fallback)
    granules = client._search_with_fallback(
        start_date=start_date,
        end_date=end_date,
        tile=tile,
    )

    if not granules:
        click.echo(f"No VJ109GA granules found for tile {tile} between {start_date} and {end_date}.")
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
    default=VJ109GA.DEFAULT_MISSION,
    show_default=True,
    help="Target mission (e.g., NOAA20, SNPP, NOAA21).",
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
    """Download available VJ109GA granules for a tile across a date range."""
    client = VJ109GA(
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
        click.echo(f"No VJ109GA granules found to download for tile {tile} between {start_date} and {end_date}.")
        return

    click.echo(f"Downloading {len(granules)} granule(s) to {client.download_directory}...")
    saved_paths = client.download_granules(granules)
    for path in saved_paths:
        click.echo(f"  {path}")


if __name__ == "__main__":
    main()