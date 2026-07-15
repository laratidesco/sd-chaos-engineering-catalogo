.DEFAULT_GOAL := help

VENV       := load-test/.venv
PYTHON     := $(CURDIR)/$(VENV)/bin/python3
PIP        := $(CURDIR)/$(VENV)/bin/pip

.PHONY: help check-tools cluster-up build \
	build-api-gateway build-api-gateway-naive build-product-service \
	helm-repos chaos-mesh monitoring venv deploy wait \
	pause-naive resume-naive pause-resilient resume-resilient \
	experiments experiments-resilient experiments-naive comparison-charts overlay-charts \
	report report-tecnico status clean-chaos all

help: ## Mostra esta ajuda
	@echo "Uso: make <alvo>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Fluxo tipico do zero:  make all"
	@echo "(sobe o cluster, builda as imagens, instala Chaos Mesh + Prometheus/Grafana,"
	@echo " aplica os manifestos, roda os 6 experimentos (3 resilientes + 3 naive,"
	@echo " ~20min no total) e gera docs/resultado-teste-<data-hora>.pdf com os"
	@echo " resultados dessa execucao. O relatorio tecnico curado e um alvo a parte:"
	@echo " make report-tecnico)"

check-tools: ## Verifica se minikube, kubectl, helm, docker, k6 e python3 estao instalados
	@command -v minikube >/dev/null || { echo "minikube nao encontrado. https://minikube.sigs.k8s.io/docs/start/"; exit 1; }
	@command -v kubectl  >/dev/null || { echo "kubectl nao encontrado. https://kubernetes.io/docs/tasks/tools/"; exit 1; }
	@command -v helm     >/dev/null || { echo "helm nao encontrado. https://helm.sh/docs/intro/install/"; exit 1; }
	@command -v docker   >/dev/null || { echo "docker nao encontrado (necessario Docker Desktop ou daemon equivalente)"; exit 1; }
	@command -v k6       >/dev/null || { echo "k6 nao encontrado. macOS: brew install k6 | Linux: https://k6.io/docs/get-started/installation/"; exit 1; }
	@command -v python3  >/dev/null || { echo "python3 nao encontrado."; exit 1; }
	@echo "Todas as ferramentas necessarias estao instaladas."

cluster-up: check-tools ## Sobe (ou reaproveita) o cluster Minikube
	@minikube status >/dev/null 2>&1 && echo "Minikube ja esta rodando." || minikube start
	minikube addons enable metrics-server

build: cluster-up build-api-gateway build-api-gateway-naive build-product-service ## Builda as 3 imagens direto no daemon Docker do Minikube

build-api-gateway: ## Builda a imagem api-gateway:latest (gateway resiliente)
	eval $$(minikube docker-env) && docker build -t api-gateway:latest ./api-gateway

build-api-gateway-naive: ## Builda a imagem api-gateway-naive:latest (sem circuit breaker/retry/timeout)
	eval $$(minikube docker-env) && docker build -t api-gateway-naive:latest ./api-gateway-naive

build-product-service: ## Builda a imagem product-service:latest (usada por product-service e product-service-naive)
	eval $$(minikube docker-env) && docker build -t product-service:latest ./product-service

helm-repos: ## Registra os repos Helm do Chaos Mesh e do kube-prometheus-stack
	helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null 2>&1 || true
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
	helm repo update >/dev/null

chaos-mesh: cluster-up helm-repos ## Instala o Chaos Mesh (namespace chaos-mesh, runtime docker) se ainda nao estiver instalado
	@helm status chaos-mesh -n chaos-mesh >/dev/null 2>&1 && echo "Chaos Mesh ja instalado." || \
		helm install chaos-mesh chaos-mesh/chaos-mesh \
			--namespace=chaos-mesh --create-namespace \
			--set chaosDaemon.runtime=docker \
			--set chaosDaemon.socketPath=/var/run/docker.sock

monitoring: cluster-up helm-repos ## Instala o kube-prometheus-stack (Prometheus + Grafana, namespace monitoring) se ainda nao estiver instalado
	@helm status monitoring -n monitoring >/dev/null 2>&1 && echo "kube-prometheus-stack ja instalado." || \
		helm install monitoring prometheus-community/kube-prometheus-stack \
			--namespace=monitoring --create-namespace
	kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=prometheus -n monitoring --timeout=180s

venv: ## Cria um virtualenv em load-test/.venv com as dependencias de load-test/requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip >/dev/null
	$(PIP) install -r load-test/requirements.txt

deploy: cluster-up build ## Aplica os manifestos k8s (deployments, services, HPA, ServiceMonitors, dashboard)
	kubectl apply -R -f k8s/deployments/
	kubectl apply -R -f k8s/services/
	kubectl apply -R -f k8s/monitoring/

wait: ## Espera todos os deployments ficarem prontos (+ 40s para o Prometheus acumular pelo menos 2 scrapes das apps)
	kubectl wait --for=condition=available --timeout=180s \
		deployment/postgres \
		deployment/product-service deployment/product-service-naive \
		deployment/api-gateway deployment/api-gateway-naive
	@echo "Aguardando 40s para o Prometheus acumular amostras das apps antes dos experimentos..."
	sleep 40

# As duas stacks compartilham o mesmo node do Minikube. Rodar as duas ao mesmo tempo faz uma
# competir por CPU com a outra e contamina as metricas (latencia/falhas sobem nas duas so por
# contencao de recurso, nao pela falha injetada). Por isso cada bateria de experimentos pausa
# (escala a 0) a stack que nao esta sendo testada e a restaura ao final - um semaforo simples.
pause-naive: ## Escala a stack naive a 0 replicas (uso interno, libera recursos para os testes resilientes)
	kubectl scale deployment/api-gateway-naive --replicas=0
	kubectl scale deployment/product-service-naive --replicas=0
	kubectl wait --for=delete pod -l app=api-gateway-naive --timeout=60s 2>/dev/null || true
	kubectl wait --for=delete pod -l app=product-service-naive --timeout=60s 2>/dev/null || true

resume-naive: ## Restaura a stack naive (1 replica cada) (uso interno)
	kubectl scale deployment/api-gateway-naive --replicas=1
	kubectl scale deployment/product-service-naive --replicas=1
	kubectl wait --for=condition=available --timeout=90s deployment/api-gateway-naive deployment/product-service-naive

pause-resilient: ## Escala a stack resiliente a 0 replicas (uso interno, libera recursos para os testes naive)
	kubectl delete hpa product-service-hpa --ignore-not-found
	kubectl scale deployment/api-gateway --replicas=0
	kubectl scale deployment/product-service --replicas=0
	kubectl wait --for=delete pod -l app=api-gateway --timeout=60s 2>/dev/null || true
	kubectl wait --for=delete pod -l app=product-service --timeout=60s 2>/dev/null || true

resume-resilient: ## Restaura a stack resiliente (2 replicas cada + HPA) (uso interno)
	kubectl scale deployment/api-gateway --replicas=2
	kubectl scale deployment/product-service --replicas=2
	kubectl apply -f k8s/deployments/resiliente/product-service-hpa.yaml
	kubectl wait --for=condition=available --timeout=90s deployment/api-gateway deployment/product-service

experiments: experiments-resilient experiments-naive ## Roda os 3 experimentos de caos nas duas stacks (resiliente + naive), uma de cada vez (~9min cada)

experiments-resilient: venv pause-naive ## Roda os 3 experimentos de caos contra a stack resiliente -> load-test/results/ (stack naive pausada)
	cd load-test && $(PYTHON) run_all.py; status=$$?; \
	$(MAKE) -C $(CURDIR) resume-naive; \
	exit $$status

experiments-naive: venv pause-resilient ## Roda os 3 experimentos de caos contra a stack naive (sem tolerancia) -> load-test/results-naive/ (stack resiliente pausada)
	cd load-test && $(PYTHON) run_all_naive.py; status=$$?; \
	$(MAKE) -C $(CURDIR) resume-resilient; \
	exit $$status

comparison-charts: venv ## Gera os 2 graficos sobrepostos (resiliente x naive na mesma imagem) usados no relatorio rapido
	cd load-test && $(PYTHON) generate_comparison_charts.py

overlay-charts: venv ## Gera os 5 graficos sobrepostos (resiliente ao fundo, naive em destaque) usados na Secao 6 do relatorio tecnico
	$(PYTHON) docs/generate_overlay_charts.py

report: venv comparison-charts ## Gera docs/resultado-teste-<data-hora>.pdf com os resultados desta execucao (sem/com tolerancia + comparativo)
	$(PIP) install fpdf2 >/dev/null
	$(PYTHON) docs/generate_resultado_teste.py

report-tecnico: venv overlay-charts ## Regenera o relatorio tecnico completo docs/relatorio-tecnico-resiliencia.pdf (curado, nome fixo)
	$(PIP) install fpdf2 >/dev/null
	$(PYTHON) docs/generate_relatorio.py

status: ## Mostra o estado atual do cluster (pods, HPA, releases Helm)
	@echo "--- pods ---"; kubectl get pods
	@echo "--- hpa ---"; kubectl get hpa
	@echo "--- helm releases ---"; helm list -A
	@echo "--- recursos de caos residuais ---"; kubectl get networkchaos,podchaos,stresschaos --all-namespaces

clean-chaos: ## Remove qualquer recurso de caos residual (seguranca, caso um experimento tenha sido interrompido)
	kubectl delete -R -f k8s/chaos/ --ignore-not-found

all: cluster-up chaos-mesh monitoring deploy wait experiments report ## Pipeline completo do zero: cluster, imagens, Helm, deploy, experimentos e relatorio do teste
	@echo ""
	@echo "Concluido. Resultados em load-test/results/ e load-test/results-naive/."
	@echo "Relatorio desta execucao em docs/resultado-teste-<data-hora>.pdf."
