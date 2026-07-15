"""StressChaos driver for the api-gateway-naive stack: injects CPU stress into
product-service-naive, which has no HorizontalPodAutoscaler (fixed at 1
replica) - unlike product-service (2-5 replicas via HPA), there is no
elasticity to absorb the overload."""

import json
import time
from pathlib import Path

import chaos_lib as lib

MANIFEST = Path(__file__).parent.parent / "k8s" / "chaos" / "naive" / "stress-chaos-naive.yaml"
RESULTS_DIR = Path(__file__).parent / "results-naive" / "stress-chaos"
GATEWAY_URL = "http://localhost:8081"

BASELINE_SECONDS = 30
ATTACK_SECONDS = 120  # matches the manifest's own duration
RECOVERY_SECONDS = 90
TOTAL_K6_SECONDS = BASELINE_SECONDS + ATTACK_SECONDS + RECOVERY_SECONDS


def sample():
    result = {"pod_count": None}
    try:
        pods = lib.kubectl_json("get", "pods", "-l", "app=product-service-naive")
        result["pod_count"] = len(pods.get("items", []))
    except RuntimeError:
        pass
    return result


def run():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    k6_proc, k6_log = lib.start_k6(TOTAL_K6_SECONDS, RESULTS_DIR, base_url=GATEWAY_URL)
    timeline_path = RESULTS_DIR / "timeline.jsonl"
    poller = lib.Poller(sample, timeline_path, interval=2.0).start()

    time.sleep(BASELINE_SECONDS)

    t_attack_start = lib.apply_chaos(MANIFEST)
    time.sleep(ATTACK_SECONDS)
    t_attack_end = lib.delete_chaos(MANIFEST)

    time.sleep(RECOVERY_SECONDS)
    steady = lib.wait_steady_state(
        timeout=90, gateway_deployment="api-gateway-naive",
        product_service_deployment="product-service-naive",
        require_circuit_closed=False, base_url=GATEWAY_URL,
    )

    poller.stop()
    k6_proc.wait(timeout=15)
    k6_log.close()

    t_end = time.time()

    max_pods = 0
    pods_series = []
    with open(timeline_path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("pod_count") is not None:
                max_pods = max(max_pods, rec["pod_count"])
                pods_series.append((rec["t"] - t_start, rec["pod_count"]))

    attack_window = (t_attack_start - t_start, t_attack_end - t_start)
    charts_dir = RESULTS_DIR / "charts"
    lib.plot_step(
        pods_series, "Pods do product-service-naive (sem HPA)", "pods",
        charts_dir / "replicas.png", attack_window,
        hlines=[(1, "replicas fixo (sem HPA)")], label="pods atuais",
    )

    for job, app_label in (
        ("api-gateway-naive", "api-gateway-naive"),
        ("product-service-naive", "product-service-naive"),
    ):
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

    events = {
        "experiment": "stress-chaos-naive",
        "t_start": t_start,
        "t_attack_start": t_attack_start,
        "t_attack_end": t_attack_end,
        "t_end": t_end,
        "steady_state_confirmed": steady,
        "max_pods_observed": max_pods,
        "manifest": str(MANIFEST),
        "config": {"workers": 2, "load": 100, "duration": "120s"},
    }
    (RESULTS_DIR / "events.json").write_text(json.dumps(events, indent=2))
    return events


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
