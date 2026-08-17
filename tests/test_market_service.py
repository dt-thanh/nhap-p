from src.services.market import SimulationMarketRepository


def test_market_starts_with_all_units_locked():
    repo = SimulationMarketRepository()
    snapshot = repo.snapshot()
    assert snapshot["phase"]["kind"] == "idle"
    assert snapshot["metrics"]["active_total"] == 100
    assert snapshot["metrics"]["locked"] == 100


def test_phase_can_only_move_forward():
    repo = SimulationMarketRepository()
    try:
        repo.change_phase("previous", True, "Admin test")
    except ValueError as exc:
        assert str(exc) == "phase_only_forward"
    else:
        raise AssertionError("previous phase change should be rejected")


def test_sale_scenario_changes_booking_to_transacted():
    repo = SimulationMarketRepository()
    repo.change_phase("next", True, "Admin test")
    booking_snapshot = repo.snapshot()
    assert booking_snapshot["phase"]["id"] == "booking_1"
    assert booking_snapshot["metrics"]["booking"] > 0

    repo.change_phase("next", True, "Admin test")
    sale_snapshot = repo.snapshot()
    before = sale_snapshot["metrics"]["transacted"]
    assert sale_snapshot["phase"]["id"] == "sale_1"
    assert sale_snapshot["metrics"]["booking"] > 0

    result = repo.run_scenario("buying_wave", 40, True, "Admin test")
    assert result["affected"] > 0
    assert result["snapshot"]["metrics"]["transacted"] > before
