# Resumo dos Experimentos de Caos

## Estado Estável (baseline)

- **api-gateway**: latência média 0.03317899524881088 s, taxa de erro 0.0, CPU 0.08005386905326281 cores, memória 165867520.0 bytes
- **product-service**: latência média 0.020497580659694377 s, taxa de erro 0.0, CPU 0.10100718110688274 cores, memória 219332608.0 bytes
- **product-service réplicas**: {'ready': 2, 'desired': 2}

## network-chaos

- Steady state confirmado após o ataque: True
- Configuração do ataque: `{'latency': '2s', 'jitter': '500ms', 'correlation': '25', 'duration': '60s'}`

## pod-chaos

- Steady state confirmado após o ataque: True
- Tempo de detecção: 0.22570395469665527 s
- Tempo de recuperação: 8.989207029342651 s
- Configuração do ataque: `{'action': 'pod-kill', 'mode': 'one', 'gracePeriod': 0}`

## stress-chaos

- Steady state confirmado após o ataque: True
- Máximo de réplicas observado: 5
- Configuração do ataque: `{'workers': 2, 'load': 100, 'duration': '120s'}`
