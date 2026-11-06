import numpy as np
import pytest

from VCD43NRT import smoke_test


def test_smoke_test_runs_real_binding(capsys):
    smoke_test.main()
    assert "Rust BRDF smoke test passed:" in capsys.readouterr().out


@pytest.mark.parametrize("result", [
    np.zeros((1, 8)),
    np.full((1, 9), np.nan),
    np.array([[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 12.0]]),
])
def test_smoke_test_rejects_bad_solver_output(monkeypatch, result):
    monkeypatch.setattr(smoke_test._brdf_rust, "nrt_brdf_all", lambda *args, **kwargs: result)
    with pytest.raises(AssertionError):
        smoke_test.main()
