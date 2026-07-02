"""Recomputes baseline stats and per-experiment charts/summary using the
corrected Prometheus queries in chaos_lib.py, WITHOUT re-running the actual
chaos attacks. Reuses the timestamps already recorded in each experiment's
events.json / baseline_stats.json. Only useful shortly after a run_all.py
execution, while Prometheus still has that time window in its TSDB."""

import json
from pathlib import Path

import chaos_lib as lib
import run_all

RESULTS_DIR = Path(__file__).parent / "results"


def _last_value(result):
    if not result:
        return None
    values = result[0]["values"]
    return float(values[-1][1]) if values else None


def regenerate_baseline():
    path = RESULTS_DIR / "baseline" / "baseline_stats.json"
    stats = json.loads(path.read_text())
    t_start, t_end = stats["t_start"], stats["t_end"]
    for job, app_label in (("api-gateway", "api-gateway"), ("product-service", "product-service")):
        stats[job] = {
            "mean_latency_seconds": _last_value(
                lib.query_prometheus_range(lib.mean_latency_query(job), t_start, t_end)
            ),
            "error_rate": _last_value(
                lib.query_prometheus_range(lib.error_rate_query(job), t_start, t_end)
            ),
            "cpu_cores": _last_value(
                lib.query_prometheus_range(lib.cpu_query(app_label), t_start, t_end)
            ),
            "memory_bytes": _last_value(
                lib.query_prometheus_range(lib.memory_query(app_label), t_start, t_end)
            ),
        }
    path.write_text(json.dumps(stats, indent=2))
    return stats


def regenerate_network_chaos():
    events = json.loads((RESULTS_DIR / "network-chaos" / "events.json").read_text())
    t_start, t_end = events["t_start"], events["t_end"]
    attack_window = (events["t_attack_start"] - t_start, events["t_attack_end"] - t_start)
    charts_dir = RESULTS_DIR / "network-chaos" / "charts"
    for job, app_label in (("api-gateway", "api-gateway"), ("product-service", "product-service")):
        latency = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.mean_latency_query(job), t_start, t_end), t_start
        )
        slow_frac = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.slow_fraction_query(job), t_start, t_end), t_start
        )
        errors = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.error_rate_query(job), t_start, t_end), t_start
        )
        cpu = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.cpu_query(app_label), t_start, t_end), t_start
        )
        mem = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.memory_query(app_label), t_start, t_end), t_start
        )
        lib.plot_timeseries(
            [latency, slow_frac], f"Latência — {job}", "segundos / fração",
            charts_dir / f"latency-{job}.png", attack_window,
            labels=["latência média (s)", "fração de requisições > 1s"],
        )
        lib.plot_timeseries(
            [errors], f"Taxa de erro (4xx/5xx) — {job}", "fração de requisições",
            charts_dir / f"error_rate-{job}.png", attack_window,
        )
        lib.plot_timeseries([cpu], f"CPU — {job}", "cores", charts_dir / f"cpu-{job}.png", attack_window)
        lib.plot_timeseries([mem], f"Memória — {job}", "bytes", charts_dir / f"memory-{job}.png", attack_window)


def regenerate_pod_chaos():
    events = json.loads((RESULTS_DIR / "pod-chaos" / "events.json").read_text())
    t_start, t_end = events["t_start"], events["t_end"]
    attack_window = (events["t_attack"] - t_start, events["t_attack"] - t_start + 1)
    charts_dir = RESULTS_DIR / "pod-chaos" / "charts"
    cpu = lib.prom_series_to_points(
        lib.query_prometheus_range(lib.cpu_query("product-service"), t_start, t_end), t_start
    )
    mem = lib.prom_series_to_points(
        lib.query_prometheus_range(lib.memory_query("product-service"), t_start, t_end), t_start
    )
    errors = lib.prom_series_to_points(
        lib.query_prometheus_range(lib.error_rate_query("api-gateway"), t_start, t_end), t_start
    )
    lib.plot_timeseries([cpu], "CPU — product-service", "cores", charts_dir / "cpu.png", attack_window)
    lib.plot_timeseries([mem], "Memória — product-service", "bytes", charts_dir / "memory.png", attack_window)
    lib.plot_timeseries(
        [errors], "Taxa de erro (4xx/5xx) — api-gateway", "fração de requisições",
        charts_dir / "error_rate.png", attack_window,
    )


def regenerate_stress_chaos():
    events = json.loads((RESULTS_DIR / "stress-chaos" / "events.json").read_text())
    t_start, t_end = events["t_start"], events["t_end"]
    attack_window = (events["t_attack_start"] - t_start, events["t_attack_end"] - t_start)
    charts_dir = RESULTS_DIR / "stress-chaos" / "charts"
    for job, app_label in (("api-gateway", "api-gateway"), ("product-service", "product-service")):
        latency = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.mean_latency_query(job), t_start, t_end), t_start
        )
        errors = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.error_rate_query(job), t_start, t_end), t_start
        )
        cpu = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.cpu_query(app_label), t_start, t_end), t_start
        )
        mem = lib.prom_series_to_points(
            lib.query_prometheus_range(lib.memory_query(app_label), t_start, t_end), t_start
        )
        lib.plot_timeseries(
            [latency], f"Latência média — {job}", "segundos",
            charts_dir / f"latency-{job}.png", attack_window,
        )
        lib.plot_timeseries(
            [errors], f"Taxa de erro (4xx/5xx) — {job}", "fração de requisições",
            charts_dir / f"error_rate-{job}.png", attack_window,
        )
        lib.plot_timeseries([cpu], f"CPU — {job}", "cores", charts_dir / f"cpu-{job}.png", attack_window)
        lib.plot_timeseries([mem], f"Memória — {job}", "bytes", charts_dir / f"memory-{job}.png", attack_window)


def main():
    baseline = regenerate_baseline()
    regenerate_network_chaos()
    regenerate_pod_chaos()
    regenerate_stress_chaos()

    results = [
        json.loads((RESULTS_DIR / exp / "events.json").read_text())
        for exp in ("network-chaos", "pod-chaos", "stress-chaos")
    ]
    run_all.write_summary(baseline, results)
    print("Regenerated baseline stats, charts and summary.")


if __name__ == "__main__":
    main()
