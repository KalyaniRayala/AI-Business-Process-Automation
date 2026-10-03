"""The metrics registry and its Prometheus exposition."""

import pytest

from app.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    Registry,
    escape_label_value,
    format_number,
)


@pytest.fixture
def registry():

    return Registry()


# ============================================================
# COUNTER
# ============================================================

def test_a_counter_adds_up(registry):

    requests = registry.counter("requests_total", "Requests.", ("route",))

    requests.inc(route="/health")
    requests.inc(route="/health")
    requests.inc(2, route="/metrics")

    assert requests.value(route="/health") == 2
    assert requests.value(route="/metrics") == 2


def test_an_unrecorded_label_reads_as_zero(registry):

    requests = registry.counter("requests_total", "Requests.", ("route",))

    assert requests.value(route="/never-called") == 0


def test_a_counter_cannot_decrease(registry):

    requests = registry.counter("requests_total", "Requests.")

    with pytest.raises(ValueError, match="cannot decrease"):
        requests.inc(-1)


def test_wrong_labels_are_rejected(registry):
    """A typo in a label name must fail loudly, not create a series."""

    requests = registry.counter("requests_total", "Requests.", ("route",))

    with pytest.raises(ValueError, match="takes labels"):
        requests.inc(path="/health")

    with pytest.raises(ValueError, match="takes labels"):
        requests.inc()


def test_an_unlabelled_counter_works(registry):

    errors = registry.counter("errors_total", "Errors.")

    errors.inc()
    errors.inc()

    assert errors.value() == 2


# ============================================================
# GAUGE
# ============================================================

def test_a_gauge_goes_up_and_down(registry):

    in_progress = registry.gauge("in_progress", "In progress.")

    in_progress.inc()
    in_progress.inc()
    in_progress.dec()

    assert in_progress.value() == 1

    in_progress.set(7)

    assert in_progress.value() == 7


# ============================================================
# HISTOGRAM
# ============================================================

def test_a_histogram_counts_sums_and_averages(registry):

    duration = registry.histogram("duration_seconds", "Duration.", ("route",))

    duration.observe(0.1, route="/health")
    duration.observe(0.3, route="/health")

    assert duration.count(route="/health") == 2
    assert duration.total_seconds(route="/health") == pytest.approx(0.4)
    assert duration.average(route="/health") == pytest.approx(0.2)


def test_the_average_of_nothing_is_zero(registry):

    duration = registry.histogram("duration_seconds", "Duration.")

    assert duration.average() == 0.0


def test_buckets_are_cumulative(registry):

    duration = registry.histogram(
        "duration_seconds", "Duration.", buckets=(0.1, 1.0)
    )

    duration.observe(0.05)
    duration.observe(0.5)
    duration.observe(5.0)

    buckets = {
        values[-1]: value
        for name, values, value in duration.samples()
        if name.endswith("_bucket")
    }

    assert buckets["0.1"] == 1
    assert buckets["1"] == 2
    assert buckets["+Inf"] == 3


def test_an_observation_on_a_bucket_boundary_is_inside_it(registry):

    duration = registry.histogram(
        "duration_seconds", "Duration.", buckets=(0.1,)
    )

    duration.observe(0.1)

    buckets = {
        values[-1]: value
        for name, values, value in duration.samples()
        if name.endswith("_bucket")
    }

    assert buckets["0.1"] == 1


# ============================================================
# REGISTRY
# ============================================================

def test_a_metric_cannot_be_registered_twice(registry):

    registry.counter("requests_total", "Requests.")

    with pytest.raises(ValueError, match="already registered"):
        registry.counter("requests_total", "Requests again.")


def test_clear_keeps_the_definitions_but_drops_the_values(registry):

    requests = registry.counter("requests_total", "Requests.", ("route",))
    duration = registry.histogram("duration_seconds", "Duration.")

    requests.inc(route="/health")
    duration.observe(1.0)

    registry.clear()

    assert requests.value(route="/health") == 0
    assert duration.count() == 0
    assert "requests_total" in registry.render()


# ============================================================
# EXPOSITION
# ============================================================

def test_render_has_help_and_type_for_every_metric(registry):

    registry.counter("requests_total", "How many requests.", ("route",)).inc(
        route="/health"
    )

    text = registry.render()

    assert "# HELP requests_total How many requests." in text
    assert "# TYPE requests_total counter" in text
    assert 'requests_total{route="/health"} 1' in text


def test_render_writes_the_three_histogram_series(registry):

    registry.histogram(
        "duration_seconds", "Duration.", ("route",), buckets=(1.0,)
    ).observe(0.5, route="/health")

    text = registry.render()

    assert 'duration_seconds_bucket{route="/health",le="1"} 1' in text
    assert 'duration_seconds_bucket{route="/health",le="+Inf"} 1' in text
    assert 'duration_seconds_sum{route="/health"} 0.5' in text
    assert 'duration_seconds_count{route="/health"} 1' in text


def test_render_ends_with_a_newline(registry):

    registry.counter("requests_total", "Requests.").inc()

    assert registry.render().endswith("\n")


def test_an_unlabelled_metric_renders_without_braces(registry):

    registry.gauge("in_progress", "In progress.").set(3)

    assert "in_progress 3" in registry.render()


@pytest.mark.parametrize(
    "value, expected",
    [(1.0, "1"), (0.5, "0.5"), (float("inf"), "+Inf"), (0, "0")],
)
def test_numbers_are_formatted_the_way_prometheus_expects(value, expected):

    assert format_number(value) == expected


def test_label_values_are_escaped():

    assert escape_label_value('a"b') == 'a\\"b'
    assert escape_label_value("a\\b") == "a\\\\b"
    assert escape_label_value("a\nb") == "a\\nb"


def test_a_quoted_label_still_renders_one_sample_per_line(registry):

    registry.counter("requests_total", "Requests.", ("route",)).inc(
        route='/odd"path'
    )

    samples = [
        line
        for line in registry.render().splitlines()
        if line.startswith("requests_total{")
    ]

    assert samples == ['requests_total{route="/odd\\"path"} 1']


# ============================================================
# SNAPSHOT
# ============================================================

def test_the_snapshot_describes_counters_and_histograms(registry):

    registry.counter("requests_total", "Requests.", ("route",)).inc(
        route="/health"
    )
    registry.histogram("duration_seconds", "Duration.", ("route",)).observe(
        0.25, route="/health"
    )

    snapshot = registry.snapshot()

    assert snapshot["requests_total"]["type"] == "counter"
    assert snapshot["requests_total"]["series"] == [
        {"labels": {"route": "/health"}, "value": 1.0}
    ]

    duration = snapshot["duration_seconds"]["series"][0]

    assert duration["count"] == 1
    assert duration["average_seconds"] == pytest.approx(0.25)


def test_the_snapshot_is_json_serialisable(registry):

    import json

    registry.counter("requests_total", "Requests.", ("route",)).inc(route="/x")

    json.dumps(registry.snapshot())


# ============================================================
# CONCURRENCY
# ============================================================

def test_counting_from_several_threads_loses_nothing(registry):

    import threading

    requests = registry.counter("requests_total", "Requests.")

    def work():
        for _ in range(500):
            requests.inc()

    threads = [threading.Thread(target=work) for _ in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert requests.value() == 2000
