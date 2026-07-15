"""StressChaos driver: injects CPU stress into product-service and watches
the HPA scale replicas out (and, if the window allows, back down)."""

import json
import time
from pathlib import Path

import chaos_lib as lib

MANIFEST = Path(__file__).parent.parent / "k8s" / "chaos" / "resiliente" / "stress-chaos.yaml"
RESULTS_DIR = Path(__file__).parent / "results" / "stress-chaos"

BASELINE_SECONDS = 30
ATTACK_SECONDS = 120  # matches the manifest's own duration
RECOVERY_SECONDS = 90
TOTAL_K6_SECONDS = BASELINE_SECONDS + ATTACK_SECONDS + RECOVERY_SECONDS


def sample():
    result = {
        "current_replicas": None,
        "desired_replicas": None,
        "cpu_utilization_pct": None,
        "pod_count": None,
    }
    try:
        hpa = lib.kubectl_json("get", "hpa", "product-service-hpa")
        status = hpa.get("status", {})
        result["current_replicas"] = status.get("currentReplicas")
        result["desired_replicas"] = status.get("desiredReplicas")
        metrics = status.get("currentMetrics", [])
        if metrics:
            result["cpu_utilization_pct"] = (
                metrics[0].get("resource", {}).get("current", {}).get("averageUtilization")
            )
    except RuntimeError:
        pass
    try:
        pods = lib.kubectl_json("get", "pods", "-l", "app=product-service")
        result["pod_count"] = len(pods.get("items", []))
    except RuntimeError:
        pass
    return result


def run():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    k6_proc, k6_log = lib.start_k6(TOTAL_K6_SECONDS, RESULTS_DIR)
    timeline_path = RESULTS_DIR / "timeline.jsonl"
    poller = lib.Poller(sample, timeline_path, interval=2.0).start()

    time.sleep(BASELINE_SECONDS)

    t_attack_start = lib.apply_chaos(MANIFEST)
    time.sleep(ATTACK_SECONDS)
    t_attack_end = lib.delete_chaos(MANIFEST)

    time.sleep(RECOVERY_SECONDS)
    steady = lib.wait_steady_state(timeout=90)

    poller.stop()
    k6_proc.wait(timeout=15)
    k6_log.close()

    t_end = time.time()

    max_replicas = 0
    replicas_series = []
    with open(timeline_path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("current_replicas") is not None:
                max_replicas = max(max_replicas, rec["current_replicas"])
                replicas_series.append((rec["t"] - t_start, rec["current_replicas"]))

    attack_window = (t_attack_start - t_start, t_attack_end - t_start)
    charts_dir = RESULTS_DIR / "charts"
    lib.plot_step(
        replicas_series, "Réplicas do product-service (HPA)", "réplicas",
        charts_dir / "replicas.png", attack_window,
        hlines=[(2, "minReplicas"), (5, "maxReplicas")], label="réplicas atuais",
    )

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

    events = {
        "experiment": "stress-chaos",
        "t_start": t_start,
        "t_attack_start": t_attack_start,
        "t_attack_end": t_attack_end,
        "t_end": t_end,
        "steady_state_confirmed": steady,
        "max_replicas_observed": max_replicas,
        "manifest": str(MANIFEST),
        "config": {"workers": 2, "load": 100, "duration": "120s"},
    }
    (RESULTS_DIR / "events.json").write_text(json.dumps(events, indent=2))
    return events


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
