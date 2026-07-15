"""Runs the shared baseline capture followed by the three chaos experiments
against the fully isolated naive stack: api-gateway-naive (no circuit
breaker/retry/timeout) -> product-service-naive (1 replica, no HPA), so the
results can be compared directly against load-test/results (the resilient
gateway + product-service's baseline+experiments)."""

import json
import time
from pathlib import Path

import chaos_lib as lib
import run_network_chaos_naive
import run_pod_chaos_naive
import run_stress_chaos_naive

RESULTS_DIR = Path(__file__).parent / "results-naive"
GATEWAY_URL = "http://localhost:8081"
BASELINE_SECONDS = 60


def _last_value(result):
    if not result:
        return None
    values = result[0]["values"]
    return float(values[-1][1]) if values else None


def capture_baseline():
    baseline_dir = RESULTS_DIR / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    k6_proc, k6_log = lib.start_k6(BASELINE_SECONDS, baseline_dir, base_url=GATEWAY_URL)
    k6_proc.wait(timeout=BASELINE_SECONDS + 30)
    k6_log.close()
    t_end = time.time()

    stats = {"t_start": t_start, "t_end": t_end}
    for job, app_label in (
        ("api-gateway-naive", "api-gateway-naive"),
        ("product-service-naive", "product-service-naive"),
    ):
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

    try:
        ready, desired = lib.get_deployment_ready("product-service-naive")
        stats["product_service_replicas"] = {"ready": ready, "desired": desired}
    except RuntimeError:
        stats["product_service_replicas"] = None

    (baseline_dir / "baseline_stats.json").write_text(json.dumps(stats, indent=2))
    return stats


def write_summary(baseline, results):
    (RESULTS_DIR / "summary.json").write_text(
        json.dumps({"baseline": baseline, "experiments": results}, indent=2, default=str)
    )

    lines = ["# Resumo dos Experimentos de Caos — api-gateway-naive (sem tolerância a falhas)", ""]
    lines.append("## Estado Estável (baseline)")
    lines.append("")
    for job in ("api-gateway-naive", "product-service-naive"):
        b = baseline.get(job, {})
        lines.append(
            f"- **{job}**: latência média {b.get('mean_latency_seconds')} s, "
            f"taxa de erro {b.get('error_rate')}, CPU {b.get('cpu_cores')} cores, "
            f"memória {b.get('memory_bytes')} bytes"
        )
    lines.append(f"- **product-service réplicas**: {baseline.get('product_service_replicas')}")
    lines.append("")

    for exp in results:
        lines.append(f"## {exp['experiment']}")
        lines.append("")
        lines.append(f"- Steady state confirmado após o ataque: {exp.get('steady_state_confirmed')}")
        if "detection_time_seconds" in exp:
            lines.append(f"- Tempo de detecção: {exp.get('detection_time_seconds')} s")
            lines.append(f"- Tempo de recuperação: {exp.get('recovery_time_seconds')} s")
        if "max_replicas_observed" in exp:
            lines.append(f"- Máximo de réplicas observado: {exp.get('max_replicas_observed')}")
        if "max_pods_observed" in exp:
            lines.append(f"- Máximo de pods observado (sem HPA): {exp.get('max_pods_observed')}")
        lines.append(f"- Configuração do ataque: `{exp.get('config')}`")
        lines.append("")

    (RESULTS_DIR / "summary.md").write_text("\n".join(lines))


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    gw_pf = lib.start_port_forward("svc/api-gateway-naive", 8081, 8000)
    prom_pf = lib.start_port_forward(
        "svc/monitoring-kube-prometheus-prometheus", 9090, 9090, namespace="monitoring"
    )

    try:
        print("Confirming steady state before baseline...", flush=True)
        lib.wait_steady_state(
            timeout=60, gateway_deployment="api-gateway-naive",
            product_service_deployment="product-service-naive",
            require_circuit_closed=False, base_url=GATEWAY_URL,
        )

        print("Capturing baseline...", flush=True)
        baseline = capture_baseline()

        results = []
        for name, module in (
            ("network-chaos-naive", run_network_chaos_naive),
            ("pod-chaos-naive", run_pod_chaos_naive),
            ("stress-chaos-naive", run_stress_chaos_naive),
        ):
            gw_pf = lib.ensure_port_forward_alive(gw_pf, "svc/api-gateway-naive", 8081, 8000)
            prom_pf = lib.ensure_port_forward_alive(
                prom_pf, "svc/monitoring-kube-prometheus-prometheus", 9090, 9090, namespace="monitoring"
            )
            print(f"Running {name}...", flush=True)
            events = module.run()
            results.append(events)
            print(f"Finished {name}: steady_state_confirmed={events.get('steady_state_confirmed')}", flush=True)

        write_summary(baseline, results)
        print("Done. See load-test/results-naive/summary.md", flush=True)
    finally:
        gw_pf.terminate()
        prom_pf.terminate()


if __name__ == "__main__":
    main()
