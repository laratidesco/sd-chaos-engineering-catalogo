"""Gera docs/resultado-teste-<data-hora>.pdf - um relatorio rapido de UMA
execucao do `make all` / `make report`, separado do relatorio tecnico
completo (generate_relatorio.py, que fica com nome fixo e e curado a mao).

Estrutura: secao 1 com os resultados da stack SEM tolerancia a falhas
(naive), secao 2 com os resultados da stack COM tolerancia (resiliente) e
secao 3 com uma tabela comparativa dos dois.

Run: python3 docs/generate_resultado_teste.py
"""

import json
from datetime import datetime
from pathlib import Path

import generate_relatorio as rel

DOCS_DIR = Path(__file__).parent
TITLE = "Relatorio do Teste - Chaos Engineering"

EXPERIMENTS = [
    ("network-chaos", "NetworkChaos (Falha de Rede)"),
    ("pod-chaos", "PodChaos (Falha de Instancia)"),
    ("stress-chaos", "StressChaos (Falha de Recurso)"),
]


class ResultadoTestePDF(rel.ReportPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("helvetica", "I", 8)
        self.set_text_color(*rel.GRAY)
        half_w = (self.w - self.l_margin - self.r_margin) / 2
        self.cell(half_w, 5, TITLE, border=0, align="L")
        self.cell(half_w, 5, f"Pagina {self.page_no()}", border=0, align="R")
        self.ln(8)
        self.set_text_color(0, 0, 0)


def pct(v):
    return f"{v * 100:.2f}%"


def ms(v):
    return f"{v:.0f}ms"


def load_experiment(results_dir, exp_key):
    k6 = rel.load_json(results_dir / exp_key / "k6-summary.json")["metrics"]
    events = rel.load_json(results_dir / exp_key / "events.json")
    return k6, events


def cover(pdf, ran_at):
    pdf.add_page()
    pdf.ln(70)
    pdf.set_font("helvetica", "B", 22)
    pdf.cell(0, 12, "Relatorio do Teste", align="C")
    pdf.ln(12)
    pdf.set_font("helvetica", "", 14)
    pdf.set_text_color(*rel.BLUE)
    pdf.cell(0, 9, "Chaos Engineering - Com e Sem Tolerancia a Falhas", align="C")
    pdf.ln(14)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", "", 11)
    for line in (
        "Trabalho Pratico de Sistemas Distribuidos - 2026/1",
        "Aplicacao: Catalogo de Produtos (API Gateway - Product Service - PostgreSQL)",
        "Gerado automaticamente a partir de load-test/results/ e load-test/results-naive/",
    ):
        pdf.cell(0, 6, line, align="C")
        pdf.ln(6)
    pdf.ln(10)
    pdf.set_font("helvetica", "I", 10)
    pdf.set_text_color(*rel.GRAY)
    pdf.cell(0, 6, f"Execucao dos experimentos em: {ran_at}", align="C")
    pdf.set_text_color(0, 0, 0)


def experiment_table(pdf, exp_key, exp_title, results_dir, extra_row):
    k6, events = load_experiment(results_dir, exp_key)
    d = k6["http_req_duration"]
    pdf.subsection_title(exp_title)
    rows = [
        ["Requisicoes totais", str(k6["http_reqs"]["count"])],
        ["Falhas expostas ao cliente", pct(k6["http_req_failed"]["value"])],
        ["Latencia media", ms(d["avg"])],
        ["Latencia p95", ms(d["p(95)"])],
        ["Latencia maxima", ms(d["max"])],
    ]
    rows.append(extra_row(events))
    pdf.kv_table(rows, label_w=55)


def network_extra(events):
    return ["Duracao do ataque", events.get("config", {}).get("duration", "-")]


def pod_extra(events):
    det = events.get("detection_time_seconds")
    rec = events.get("recovery_time_seconds")
    det_s = f"{det:.2f}s" if det is not None else "-"
    rec_s = f"{rec:.2f}s" if rec is not None else "-"
    return ["Deteccao / Recuperacao", f"{det_s} / {rec_s}"]


def stress_extra(events):
    if "max_replicas_observed" in events:
        return ["Replicas maximas (HPA)", str(events["max_replicas_observed"])]
    return ["Pods maximos (sem HPA)", str(events.get("max_pods_observed", "-"))]


EXTRA_FN = {
    "network-chaos": network_extra,
    "pod-chaos": pod_extra,
    "stress-chaos": stress_extra,
}

# network-chaos e stress-chaos tem grafico de latencia por job (mais informativo - mostra a
# curva subindo/descendo durante o ataque); pod-chaos nao gera latencia por job, so cpu/memoria/
# erro, e o cpu do product-service e o mais interessante dos tres (mostra a queda de 1 replica).
MAIN_CHART = {
    "network-chaos": lambda job: (f"latency-{job}.png", "Latencia media"),
    "pod-chaos": lambda job: ("cpu.png", "CPU do product-service"),
    "stress-chaos": lambda job: (f"latency-{job}.png", "Latencia media"),
}


def main_chart(pdf, exp_key, exp_title, results_dir, job, stack_label):
    filename, metric_label = MAIN_CHART[exp_key](job)
    chart_path = results_dir / exp_key / "charts" / filename
    pdf.chart(chart_path, f"{metric_label} - {exp_title} - stack {stack_label}.")


def section1(pdf):
    pdf.add_page()
    pdf.section_title("1. Resultados Sem os Mecanismos de Tolerancia a Falhas (Stack Naive)")
    pdf.body(
        "Stack api-gateway-naive -> product-service-naive: sem circuit breaker, sem retry, "
        "sem timeout no gateway; 1 replica fixa e sem HorizontalPodAutoscaler no product-service."
    )
    for exp_key, exp_title in EXPERIMENTS:
        experiment_table(pdf, exp_key, exp_title, rel.RESULTS_NAIVE, EXTRA_FN[exp_key])
        main_chart(pdf, exp_key, exp_title, rel.RESULTS_NAIVE, "api-gateway-naive", "naive")
        if exp_key == "network-chaos":
            # unico grafico de taxa de erro do relatorio: aqui e onde ela de fato sai do zero.
            pdf.chart(
                rel.RESULTS_NAIVE / exp_key / "charts" / "error_rate-api-gateway-naive.png",
                f"Taxa de erro (4xx/5xx) - {exp_title} - stack naive (unico grafico de erro "
                "do relatorio - e onde a falha realmente aparece).",
            )


def section2(pdf):
    pdf.add_page()
    pdf.section_title("2. Resultados Com os Mecanismos de Tolerancia a Falhas (Stack Resiliente)")
    pdf.body(
        "Stack api-gateway -> product-service: circuit breaker, retry com backoff exponencial e "
        "timeouts explicitos no gateway; 2 replicas e HorizontalPodAutoscaler (min 2, max 5, "
        "alvo 50% CPU) no product-service."
    )
    for exp_key, exp_title in EXPERIMENTS:
        experiment_table(pdf, exp_key, exp_title, rel.RESULTS, EXTRA_FN[exp_key])
        main_chart(pdf, exp_key, exp_title, rel.RESULTS, "api-gateway", "resiliente")


def section3(pdf):
    pdf.add_page()
    pdf.section_title("3. Tabela Comparativa")
    pdf.body(
        "Falhas expostas ao cliente e latencia (k6) das duas stacks, lado a lado, para os "
        "mesmos 3 ataques de Chaos Mesh."
    )
    pdf.body(
        "Obs.: 'naive' = stack SEM nenhum mecanismo de tolerancia a falhas (sem circuit "
        "breaker, retry, timeout ou replicas/HPA); 'resiliente' = stack COM esses mecanismos."
    )
    rows = []
    for exp_key, exp_title in EXPERIMENTS:
        k6_res, _ = load_experiment(rel.RESULTS, exp_key)
        k6_nai, _ = load_experiment(rel.RESULTS_NAIVE, exp_key)
        rows.append([
            exp_title,
            pct(k6_res["http_req_failed"]["value"]),
            pct(k6_nai["http_req_failed"]["value"]),
            ms(k6_res["http_req_duration"]["p(95)"]),
            ms(k6_nai["http_req_duration"]["p(95)"]),
        ])
    pdf.data_table(
        ["Experimento", "Falhas (resiliente)", "Falhas (naive)", "p95 (resiliente)", "p95 (naive)"],
        rows,
    )
    pdf.body(
        "Falhas expostas ao cliente e/ou latencia p95 maiores na coluna 'naive' evidenciam o "
        "impacto de remover circuit breaker, retry, timeout e replicas/HPA. Para a analise "
        "detalhada (hipotese, configuracao do ataque, evidencia de log e o porque cada mecanismo "
        "funciona), veja docs/relatorio-tecnico-resiliencia.pdf."
    )
    comparison_dir = DOCS_DIR.parent / "load-test" / "comparison-charts"
    pdf.chart(
        comparison_dir / "network-chaos-latency-comparativo.png",
        "Latencia media sobreposta - NetworkChaos: resiliente vs. naive.",
    )
    pdf.chart(
        comparison_dir / "stress-chaos-latency-comparativo.png",
        "Latencia media sobreposta - StressChaos: resiliente vs. naive.",
    )


def build():
    ran_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = DOCS_DIR / f"resultado-teste-{stamp}.pdf"

    pdf = ResultadoTestePDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 15, 20)
    cover(pdf, ran_at)
    section1(pdf)
    section2(pdf)
    section3(pdf)
    pdf.output(str(out_path))
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes, {pdf.page_no()} pages)")


if __name__ == "__main__":
    build()
