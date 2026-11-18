import pytest
from app import stats


def test_percentile_linear():
    s = [1, 2, 3, 4]
    assert stats.percentile(s, 0) == 1
    assert stats.percentile(s, 100) == 4
    assert stats.percentile(s, 50) == 2.5
    assert stats.percentile(s, 25) == 1.75  # numpy linear と一致


def test_percentile_errors():
    with pytest.raises(ValueError):
        stats.percentile([], 50)
    with pytest.raises(ValueError):
        stats.percentile([1], 101)


def test_median_odd_even():
    assert stats.median([3, 1, 2]) == 2
    assert stats.median([4, 1, 3, 2]) == 2.5


def test_remove_outliers():
    vals = [100, 102, 98, 101, 99, 103, 97, 1000, 10]
    kept, removed = stats.remove_outliers(vals)
    assert sorted(removed) == [10, 1000]
    assert 1000 not in kept and 10 not in kept


def test_remove_outliers_small_sample_untouched():
    kept, removed = stats.remove_outliers([1, 100, 1000])
    assert removed == [] and kept == [1, 100, 1000]


def test_percentile_rank():
    s = [10, 20, 30, 40]
    assert stats.percentile_rank(s, 5) == 0
    assert stats.percentile_rank(s, 50) == 100
    assert stats.percentile_rank(s, 20) == 37.5


def test_summarize():
    st = stats.summarize([10, 20, 30, 40, 50])
    assert st["n"] == 5 and st["median"] == 30 and st["q1"] == 20 and st["q3"] == 40
    assert st["iqr"] == 20 and st["dispersion"] == round(20 / 30, 3)


def test_histogram_counts_sum():
    h = stats.histogram([1, 2, 3, 4, 5, 10], bins=3)
    assert sum(b["count"] for b in h) == 6
