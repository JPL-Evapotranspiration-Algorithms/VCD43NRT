import os
import re
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple


@dataclass(frozen=True)
class VIIRSMissionConfig:
    key: str
    display_name: str
    short_name: str
    product_prefix: str
    concept_id_env: str
    default_concept_id: Optional[str] = None

    @property
    def concept_id(self) -> Optional[str]:
        override = os.environ.get(self.concept_id_env)
        if override:
            return override
        return self.default_concept_id


VIIRS_MISSIONS: Dict[str, VIIRSMissionConfig] = {
    "SNPP": VIIRSMissionConfig(
        key="SNPP",
        display_name="Suomi NPP",
        short_name="VNP09GA",
        product_prefix="VNP",
        concept_id_env="VCD43NRT_VNP09GA_CONCEPT_ID",
        default_concept_id="C2631841556-LPCLOUD",
    ),
    "JPSS1": VIIRSMissionConfig(
        key="JPSS1",
        display_name="JPSS-1",
        short_name="VJ109GA",
        product_prefix="VJ1",
        concept_id_env="VCD43NRT_VJ109GA_CONCEPT_ID",
        default_concept_id="C2631841524-LPCLOUD",
    ),
    "JPSS2": VIIRSMissionConfig(
        key="JPSS2",
        display_name="JPSS-2",
        short_name="VJ209GA",
        product_prefix="VJ2",
        concept_id_env="VCD43NRT_VJ209GA_CONCEPT_ID",
    ),
}

DEFAULT_MISSION = "SNPP"
DEFAULT_MISSION_PRIORITY: Tuple[str, ...] = ("SNPP", "JPSS1", "JPSS2")

MISSION_ALIASES = {
    "SNPP": "SNPP",
    "SUOMI": "SNPP",
    "SUOMINPP": "SNPP",
    "NPP": "SNPP",
    "NOAA20": "JPSS1",
    "NOAA-20": "JPSS1",
    "JPSS1": "JPSS1",
    "JPSS-1": "JPSS1",
    "N20": "JPSS1",
    "NOAA21": "JPSS2",
    "NOAA-21": "JPSS2",
    "JPSS2": "JPSS2",
    "JPSS-2": "JPSS2",
    "N21": "JPSS2",
}

VIIRS_FILENAME_REGEX = re.compile(r"^(VNP|VJ1|VJ2)09GA\.[^.]+\.([^.]+)\.\d{3}\.\d+\.h5$")
PREFIX_TO_MISSION = {
    "VNP": "SNPP",
    "VJ1": "JPSS1",
    "VJ2": "JPSS2",
}


def normalize_mission(mission: str) -> str:
    if mission is None:
        return DEFAULT_MISSION

    normalized = str(mission).strip().upper()
    normalized = MISSION_ALIASES.get(normalized, normalized)

    if normalized not in VIIRS_MISSIONS:
        allowed = ", ".join(sorted(VIIRS_MISSIONS))
        raise ValueError(f"Unknown VIIRS mission '{mission}'. Allowed values: {allowed}")

    return normalized


def normalize_mission_priority(
    mission_priority: Optional[Sequence[str]],
    mission: str,
    fallback_mode: bool,
) -> Tuple[str, ...]:
    if mission_priority is None:
        if fallback_mode:
            return DEFAULT_MISSION_PRIORITY
        return (mission,)

    if isinstance(mission_priority, str):
        mission_priority = [entry.strip() for entry in mission_priority.split(",") if entry.strip()]

    normalized = tuple(normalize_mission(entry) for entry in mission_priority)
    if len(normalized) == 0:
        raise ValueError("mission_priority cannot be empty")

    return tuple(dict.fromkeys(normalized))


def modland_tile_from_filename(filename: str) -> str:
    match = VIIRS_FILENAME_REGEX.match(filename)
    if match is None:
        raise RuntimeError(f"Invalid filename found through VIIRS CMR search: {filename}")

    return match.group(2)


def mission_from_filename(filename: str) -> str:
    match = VIIRS_FILENAME_REGEX.match(filename)
    if match is None:
        raise RuntimeError(f"Invalid filename found through VIIRS CMR search: {filename}")

    mission = PREFIX_TO_MISSION.get(match.group(1))
    if mission is None:
        raise RuntimeError(f"Unsupported VIIRS mission prefix in filename: {filename}")

    return mission
