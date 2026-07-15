"""NetworkChaos driver for the api-gateway-naive variant (no circuit breaker,
no retry, no client-side timeout) — same attack as run_network_chaos.py, used
to compare against the resilient gateway's results."""

import json
import time
from pathlib import Path

import chaos_lib as lib

MANIFEST = Path(__file__).parent.parent / "k8s" / "chaos" / "naive" / "network-chaos-naive.yaml"
RESULTS_DIR = Path(__file__).parent / "results-naive" / "network-chaos"
GATEWAY_URL = "http://localhost:8081"

BASELINE_SECONDS = 30
ATTACK_SECONDS = 60  # matches the manifest's own duration
RECOVERY_SECONDS = 30
TOTAL_K6_SECONDS = BASELINE_SECONDS + ATTACK_SECONDS + RECOVERY_SECONDS


def sample():
    try:
        ready, desired = lib.get_deployment_ready("product-service-naive")
    except RuntimeError:
        ready, desired = -1, -1
    return {
        "product_service_ready": ready,
        "product_service_desired": desired,
    }


def run():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    k6_proc, k6_log = lib.start_k6(TOTAL_K6_SECONDS, RESULTS_DIR, base_url=GATEWAY_URL)
    poller = lib.Poller(sample, RESULTS_DIR / "timeline.jsonl", interval=2.0).start()

    time.sleep(BASELINE_SECONDS)

    t_attack_start = lib.apply_chaos(MANIFEST)
    time.sleep(ATTACK_SECONDS)
    t_attack_end = lib.delete_chaos(MANIFEST)

    time.sleep(RECOVERY_SECONDS)
    steady = lib.wait_steady_state(
        timeout=60, gateway_deployment="api-gateway-naive",
        product_service_deployment="product-service-naive",
        require_circuit_closed=False, base_url=GATEWAY_URL,
    )

    poller.stop()
    k6_proc.wait(timeout=15)
    k6_log.close()

    t_end = time.time()
    attack_window = (t_attack_start - t_start, t_attack_end - t_start)

    charts_dir = RESULTS_DIR / "charts"
    for job, app_label in (("api-gateway-naive", "api-gateway-naive"), ("product-service-naive", "product-service-naive")):
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

    events = {
        "experiment": "network-chaos-naive",
        "t_start": t_start,
        "t_attack_start": t_attack_start,
        "t_attack_end": t_attack_end,
        "t_end": t_end,
        "steady_state_confirmed": steady,
        "manifest": str(MANIFEST),
        "config": {"latency": "2s", "jitter": "500ms", "correlation": "25", "duration": "60s"},
    }
    (RESULTS_DIR / "events.json").write_text(json.dumps(events, indent=2))
    return events


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
