import pytest

from VCD43NRT.VNP09GA import (
    DEFAULT_MISSION,
    VIIRS_MISSIONS,
    mission_from_filename,
    modland_tile_from_filename,
    normalize_mission,
    normalize_mission_priority,
)
from VCD43NRT.VIIRS import VIIRS
from VCD43NRT.VIIRS_granule import VIIRSGranule
from VCD43NRT.VNP09GA import VNP09GA
from VCD43NRT.VNP09GA_granule import VNP09GAGranule
from VCD43NRT.VJ109GA import VJ109GA
from VCD43NRT.VJ109GA_granule import VJ109GAGranule
from VCD43NRT.VJ209GA import VJ209GA
from VCD43NRT.VJ209GA_granule import VJ209GAGranule
from VCD43NRT.constants import WORKING_DIRECTORY


def test_default_working_directory_uses_central_constant():
    assert VIIRS.DEFAULT_WORKING_DIRECTORY == WORKING_DIRECTORY
    assert VNP09GA.DEFAULT_WORKING_DIRECTORY == WORKING_DIRECTORY


def test_normalize_mission_aliases():
    assert normalize_mission("SNPP") == "SNPP"
    assert normalize_mission("JPSS1") == "JPSS1"
    assert normalize_mission("jpss-2") == "JPSS2"
    assert normalize_mission("noaa-20") == "JPSS1"
    assert normalize_mission("NOAA21") == "JPSS2"


def test_normalize_mission_priority_string():
    priority = normalize_mission_priority("SNPP,JPSS1,JPSS2", mission="SNPP", fallback_mode=True)
    assert priority == ("SNPP", "JPSS1", "JPSS2")


def test_default_mission_priority_behavior():
    disabled = normalize_mission_priority(None, mission="JPSS1", fallback_mode=False)
    enabled = normalize_mission_priority(None, mission=DEFAULT_MISSION, fallback_mode=True)

    assert disabled == ("JPSS1",)
    assert enabled == ("SNPP", "JPSS1", "JPSS2")


def test_filename_parsing_for_supported_prefixes():
    snpp_name = "VNP09GA.A2026283.h08v05.002.2026284000000.h5"
    n20_name = "VJ109GA.A2026283.h08v05.002.2026284000000.h5"
    n21_name = "VJ209GA.A2026283.h08v05.002.2026284000000.h5"

    assert modland_tile_from_filename(snpp_name) == "h08v05"
    assert mission_from_filename(snpp_name) == "SNPP"
    assert mission_from_filename(n20_name) == "JPSS1"
    assert mission_from_filename(n21_name) == "JPSS2"


def test_mission_registry_contains_required_entries():
    assert "SNPP" in VIIRS_MISSIONS
    assert "JPSS1" in VIIRS_MISSIONS
    assert "JPSS2" in VIIRS_MISSIONS

    assert VIIRS_MISSIONS["SNPP"].short_name == "VNP09GA"
    assert VIIRS_MISSIONS["JPSS1"].short_name == "VJ109GA"
    assert VIIRS_MISSIONS["JPSS2"].short_name == "VJ209GA"
    assert VJ109GA.DEFAULT_MISSION == "JPSS1"
    assert VJ209GA.DEFAULT_MISSION == "JPSS2"


def test_viirs_class_hierarchy():
    assert issubclass(VNP09GAGranule, VIIRSGranule)
    assert issubclass(VJ109GAGranule, VIIRSGranule)
    assert issubclass(VJ209GAGranule, VIIRSGranule)

    assert issubclass(VNP09GA, VIIRS)
    assert issubclass(VJ109GA, VIIRS)
    assert issubclass(VJ209GA, VIIRS)


def test_mission_priority_deduplicates_aliases_and_rejects_empty():
    assert normalize_mission_priority(
        ["NOAA-20", "JPSS1", "SNPP", "npp"], mission="SNPP", fallback_mode=True
    ) == ("JPSS1", "SNPP")
    for priority in ([], "", " , "):
        with pytest.raises(ValueError, match="mission_priority cannot be empty"):
            normalize_mission_priority(priority, mission="SNPP", fallback_mode=True)
    with pytest.raises(ValueError, match="Unknown VIIRS mission"):
        normalize_mission("unknown")


@pytest.mark.parametrize("filename", ["bad.h5", "VJ309GA.A2026283.h08v05.002.2026284000000.h5"])
def test_invalid_filenames_are_rejected(filename):
    for parse in (mission_from_filename, modland_tile_from_filename):
        with pytest.raises(RuntimeError, match="Invalid filename"):
            parse(filename)


def test_concept_id_uses_environment_override(monkeypatch):
    config = VIIRS_MISSIONS["SNPP"]
    monkeypatch.delenv(config.concept_id_env, raising=False)
    assert config.concept_id == config.default_concept_id
    monkeypatch.setenv(config.concept_id_env, "custom-concept")
    assert config.concept_id == "custom-concept"
