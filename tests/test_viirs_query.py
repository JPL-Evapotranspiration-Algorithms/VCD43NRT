from datetime import date, datetime, timezone

import pytest

from VCD43NRT import VIIRS_query
from VCD43NRT.exceptions import CMRServerUnreachable


@pytest.mark.parametrize("value", [date(2026, 10, 1), "2026-10-01T14:30:00Z"])
def test_query_date_boundaries(value):
    assert VIIRS_query.earliest_datetime(value) == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert VIIRS_query.latest_datetime(value) == datetime(2026, 10, 1, 23, 59, 59, tzinfo=timezone.utc)


class FakeQuery:
    def __init__(self, granules=None, error=None, fail_attempts: int = 0):
        self.calls = []
        self.granules = granules
        self.error = error
        self.fail_attempts = fail_attempts
        self.get_calls = 0

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
            return self
        return record

    def get(self):
        self.get_calls += 1
        if self.error is not None and (self.fail_attempts == 0 or self.get_calls <= self.fail_attempts):
            raise self.error
        return self.granules


def granule(timestamp, name):
    return {
        "umm": {"TemporalExtent": {"RangeDateTime": {"BeginningDateTime": timestamp}}},
        "meta": {"native-id": name},
    }


@pytest.mark.parametrize(
    ("mission", "env", "override", "expected"),
    [
        ("SNPP", "VCD43NRT_VNP09GA_CONCEPT_ID", "test-id", ("concept_id", ("test-id",))),
        ("JPSS2", "VCD43NRT_VJ209GA_CONCEPT_ID", None, ("short_name", ("VJ209GA",))),
    ],
)
def test_query_filters_and_sorts_granules(monkeypatch, mission, env, override, expected):
    if override is None:
        monkeypatch.delenv(env, raising=False)
    else:
        monkeypatch.setenv(env, override)
    earlier = granule("2026-10-01T00:00:00Z", "earlier")
    later = granule("2026-10-02T00:00:00Z", "later")
    query = FakeQuery([later, earlier])
    monkeypatch.setattr(VIIRS_query.earthaccess, "granule_query", lambda: query)

    result = VIIRS_query.VIIRS_CMR_query("2026-10-01", "2026-10-02", mission, tile="h08v05")

    assert result == [earlier, later]
    assert expected in query.calls
    assert ("readable_granule_name", ("*.h08v05.*",)) in query.calls
    assert query.calls[0] == (
        "temporal",
        (datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 2, 23, 59, 59, tzinfo=timezone.utc)),
    )


def test_query_retries_transient_cmr_errors_and_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(VIIRS_query.time, "sleep", lambda s: sleeps.append(s))
    expected_granules = [granule("2026-10-01T00:00:00Z", "ok")]
    query = FakeQuery(
        granules=expected_granules,
        error=RuntimeError('{"errors":["An Internal Error has occurred."]}'),
        fail_attempts=2,
    )
    monkeypatch.setattr(VIIRS_query.earthaccess, "granule_query", lambda: query)

    result = VIIRS_query.VIIRS_CMR_query(
        "2026-10-01",
        "2026-10-02",
        "SNPP",
        retries=3,
        backoff_seconds=1.0,
    )

    assert result == expected_granules
    assert query.get_calls == 3
    assert len(sleeps) == 2


def test_query_reports_cmr_failure_after_exhausting_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(VIIRS_query.time, "sleep", lambda s: sleeps.append(s))
    query = FakeQuery(error=RuntimeError("network failed"))
    monkeypatch.setattr(VIIRS_query.earthaccess, "granule_query", lambda: query)
    with pytest.raises(CMRServerUnreachable, match="network failed"):
        VIIRS_query.VIIRS_CMR_query("2026-10-01", "2026-10-02", "SNPP", retries=3, backoff_seconds=1.0)
    assert query.get_calls == 3
    assert len(sleeps) == 2
