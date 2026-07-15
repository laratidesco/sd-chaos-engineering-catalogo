# Chaos Engineering — Catálogo de Produtos

Trabalho Prático de Sistemas Distribuídos (UFES/CCENS, 2026/1). Aplica Chaos
Engineering (Chaos Mesh) sobre uma aplicação de catálogo de produtos rodando
em Kubernetes, medindo o efeito real dos mecanismos de tolerância a falhas
através de 3 experimentos de caos e uma comparação com/sem esses mecanismos.

## Arquitetura

```
API Gateway (FastAPI, único ponto exposto)
      │
      ▼
Product Service (FastAPI + SQLAlchemy async + asyncpg)
      │
      ▼
PostgreSQL
```

Duas variantes da stack rodam lado a lado no mesmo cluster, para comparação:

- **Stack resiliente** (`api-gateway` → `product-service`): circuit breaker,
  retry com backoff exponencial e timeouts explícitos no gateway; 2 réplicas
  + HorizontalPodAutoscaler (min 2, max 5, alvo 50% CPU) no product-service.
- **Stack naive** (`api-gateway-naive` → `product-service-naive`): proxy
  direto, sem circuit breaker, sem retry, sem timeout; 1 réplica fixa, sem
  HPA. Serve só para medir o impacto de remover os 4 mecanismos acima.

As duas stacks compartilham apenas o PostgreSQL.

As duas rodam no mesmo cluster de 1 nó (Minikube). Para evitar que uma contamine as métricas
da outra por disputa de CPU, `make experiments` roda uma stack de cada vez: pausa (escala a 0
réplicas) a stack que não está sendo testada, roda os 3 experimentos, e restaura a stack pausada
ao final (alvos internos `pause-naive`/`resume-naive`/`pause-resilient`/`resume-resilient` no
Makefile).

## Experimentos de caos

Executados via [Chaos Mesh](https://chaos-mesh.org/), com carga contínua
gerada por [k6](https://k6.io/) durante cada ataque:

| Experimento | O que faz | Mecanismo exercitado |
|---|---|---|
| **NetworkChaos** | Injeta latência (2s, jitter 500ms) entre product-service e gateway | circuit breaker / retry / timeout |
| **PodChaos** | Mata um pod do product-service em pleno funcionamento | réplicas do product-service |
| **StressChaos** | Sobrecarrega a CPU do product-service | HorizontalPodAutoscaler |

Observabilidade via Prometheus + Grafana
([kube-prometheus-stack](https://github.com/prometheus-community/helm-charts)).

## Estrutura do repositório

```
api-gateway/            gateway resiliente (circuit breaker + retry + timeout)
api-gateway-naive/       gateway sem nenhum mecanismo de tolerância
product-service/         serviço de produtos (usado por ambas as stacks,
                         product-service-naive roda a mesma imagem com 1 réplica)
k8s/                     cada pasta abaixo se divide em resiliente/, naive/ e (quando aplicavel) shared/
  deployments/
    resiliente/          api-gateway + product-service + HPA
    naive/                api-gateway-naive + product-service-naive
    shared/              postgres (Deployment + Secret + PVC) - usado pelas duas stacks
  services/              mesma divisao (resiliente/naive/shared) para os Services
  chaos/                 manifestos do Chaos Mesh, um por experimento, resiliente/naive
  monitoring/            ServiceMonitors por stack (resiliente/naive) + dashboard do Grafana
load-test/
  run_all.py                    roda os 3 experimentos contra a stack resiliente
  run_all_naive.py              roda os 3 experimentos contra a stack naive
  run_<experimento>[_naive].py  driver de cada experimento individual (chamado por run_all*.py)
  generate_comparison_charts.py gera os 2 gráficos sobrepostos usados no relatório rápido
  comparison-charts/            gráficos gerados por generate_comparison_charts.py
  results/, results-naive/      resultados (gráficos, logs, métricas k6) de cada stack
docs/
  generate_relatorio.py        gera o relatório técnico completo (curado, nome fixo)
  generate_overlay_charts.py   gera os 5 gráficos sobrepostos usados na Seção 6 do relatório técnico
  overlay-charts/               gráficos gerados por generate_overlay_charts.py
  relatorio-tecnico-resiliencia.pdf
  generate_resultado_teste.py  gera um relatório rápido de UMA execução (nome com data/hora)
  resultado-teste-<data-hora>.pdf
Makefile                 automatiza tudo abaixo
```

## Pré-requisitos

- [Docker](https://www.docker.com/) (ou Docker Desktop)
- [Minikube](https://minikube.sigs.k8s.io/docs/start/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Helm](https://helm.sh/docs/intro/install/)
- [k6](https://k6.io/docs/get-started/installation/)
- Python 3

Testado em macOS e Linux (não há suporte a Windows).

## Como rodar tudo

```sh
git clone https://github.com/laratidesco/sd-chaos-engineering-catalogo.git
cd sd-chaos-engineering-catalogo
make all
```

`make all` sobe o Minikube, builda as 3 imagens direto no daemon Docker do
cluster, instala Chaos Mesh e o kube-prometheus-stack via Helm (só se ainda
não estiverem instalados), aplica os manifestos, espera tudo ficar pronto,
roda os 6 experimentos (3 na stack resiliente + 3 na stack naive, ~20 min no
total) e gera `docs/resultado-teste-<data-hora>.pdf` — um relatório rápido
com os resultados sem tolerância, com tolerância e uma tabela comparativa
dessa execução específica.

## Construir e subir só os containers

Se quiser apenas builda as imagens e colocar as duas stacks de pé no cluster
(sem rodar os experimentos de caos nem gerar relatório):

```sh
make cluster-up   # sobe o Minikube (reaproveita se ja estiver rodando)
make build        # builda api-gateway, api-gateway-naive e product-service no daemon do Minikube
make deploy       # aplica os manifestos k8s (deployments, services, HPA, monitoring)
make wait         # espera todos os deployments ficarem prontos
```

Ao final, `kubectl get pods` deve mostrar os 5 deployments rodando: `postgres`,
`api-gateway`, `api-gateway-naive`, `product-service` e `product-service-naive`.

Para rodar por partes (ou repetir só uma etapa), veja `make help`:

```sh
make help                   # lista todos os alvos disponíveis
make cluster-up             # sobe o Minikube (reaproveita se já estiver rodando)
make build                  # builda api-gateway, api-gateway-naive e product-service
make chaos-mesh monitoring  # instala Chaos Mesh e Prometheus/Grafana via Helm
make deploy wait            # aplica os manifestos k8s e espera tudo ficar pronto
make experiments            # roda os 3 experimentos nas duas stacks
make report                 # gera docs/resultado-teste-<data-hora>.pdf a partir dos resultados existentes
make report-tecnico         # regenera o relatório técnico completo e curado (nome fixo)
make status                 # mostra o estado atual do cluster
```

## Monitorando a execução

Enquanto `make all` (ou `make experiments`) está rodando, dá para acompanhar
em tempo real por outro terminal, sem interferir no que já está em andamento:

**Direto no Docker (containers dentro do node do Minikube):**

```sh
eval $(minikube docker-env)
docker ps                    # lista os containers rodando (um por pod)
docker stats                 # CPU/memória ao vivo de cada container
```

**Via kubectl (mais útil pra ver o que o Chaos Mesh está fazendo):**

```sh
kubectl get pods -w                                # pods mudando de estado ao vivo
kubectl get networkchaos,podchaos,stresschaos -w    # aparecem so durante o ataque ativo
kubectl top pods                                    # CPU/memoria por pod (via metrics-server)
```

**Dashboard visual do Minikube:**

```sh
minikube dashboard
```

**Grafana** (dashboard "FastAPI Observability" já provisionado):

```sh
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80
# depois abrir http://localhost:3000 (login admin/admin)
```
