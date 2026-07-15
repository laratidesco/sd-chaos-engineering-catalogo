# Resumo dos Experimentos de Caos — api-gateway-naive (sem tolerância a falhas)

## Estado Estável (baseline)

- **api-gateway-naive**: latência média 0.021725307726673245 s, taxa de erro 0.0, CPU 0.05503896218117854 cores, memória 60678144.0 bytes
- **product-service-naive**: latência média 0.012487297839029994 s, taxa de erro 0.0, CPU 0.0700111113364018 cores, memória 68755456.0 bytes
- **product-service réplicas**: {'ready': 1, 'desired': 1}

## network-chaos-naive

- Steady state confirmado após o ataque: True
- Configuração do ataque: `{'latency': '2s', 'jitter': '500ms', 'correlation': '25', 'duration': '60s'}`

## pod-chaos-naive

- Steady state confirmado após o ataque: True
- Tempo de detecção: 0.8426816463470459 s
- Tempo de recuperação: 7.243867874145508 s
- Configuração do ataque: `{'action': 'pod-kill', 'mode': 'one', 'gracePeriod': 0}`

## stress-chaos-naive

- Steady state confirmado após o ataque: True
- Máximo de pods observado (sem HPA): 1
- Configuração do ataque: `{'workers': 2, 'load': 100, 'duration': '120s'}`
