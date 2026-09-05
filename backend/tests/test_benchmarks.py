"""Tests for the BSE SME benchmark fixture loader (Day 4)."""

import pytest

from backend.app.ingest.benchmarks import BenchmarkPoint, BSEFixtureBenchmark


@pytest.mark.asyncio
async def test_fixture_loads_at_least_one_point() -> None:
    source = BSEFixtureBenchmark()
    points = await source.fetch_all()
    assert len(points) >= 10, "Expected fixture to yield benchmarks for several NIC codes"


@pytest.mark.asyncio
async def test_percentiles_are_monotonic_per_point() -> None:
    points = await BSEFixtureBenchmark().fetch_all()
    for p in points:
        assert p.p25 <= p.median <= p.p75, (
            f"Percentiles out of order for NIC {p.nic_code}/{p.metric}: "
            f"p25={p.p25}, median={p.median}, p75={p.p75}"
        )


@pytest.mark.asyncio
async def test_benford_flag_false_for_fixed_price_industries() -> None:
    """NIC 46/47/49 are fixed-price retail / wholesale / transport - Benford disabled."""
    points = await BSEFixtureBenchmark().fetch_all()
    fixed_price_nics = {46101, 46190, 49120}
    for p in points:
        if p.nic_code in fixed_price_nics:
            assert p.benford_applicable is False, (
                f"NIC {p.nic_code} should be Benford-disabled per PRD section 4.3"
            )


@pytest.mark.asyncio
async def test_includes_steel_trading_27101() -> None:
    points = await BSEFixtureBenchmark().fetch_all()
    nics_seen = {p.nic_code for p in points}
    assert 27101 in nics_seen  # Steel trading - used by ABC fixture's peer comparison


def test_benchmark_point_rejects_invalid_year() -> None:
    with pytest.raises(Exception):
        BenchmarkPoint(nic_code=27101, year=1800, metric="x", p25=0, median=0, p75=0)


def test_benchmark_point_key_property() -> None:
    p = BenchmarkPoint(nic_code=27101, year=2023, metric="m", p25=1, median=2, p75=3)
    assert p.key == (27101, 2023, "m")
