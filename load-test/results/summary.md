# Resumo dos Experimentos de Caos

## Estado Estável (baseline)

- **api-gateway**: latência média 0.0091422134987602 s, taxa de erro 0.0, CPU 0.033474673263612614 cores, memória 111710208.0 bytes
- **product-service**: latência média 0.0038876135975200355 s, taxa de erro 0.0, CPU 0.034474125982729954 cores, memória 130801664.0 bytes
- **product-service réplicas**: {'ready': 2, 'desired': 2}

## network-chaos

- Steady state confirmado após o ataque: True
- Configuração do ataque: `{'latency': '2s', 'jitter': '500ms', 'correlation': '25', 'duration': '60s'}`

## pod-chaos

- Steady state confirmado após o ataque: True
- Tempo de detecção: 0.25466084480285645 s
- Tempo de recuperação: 7.801687002182007 s
- Configuração do ataque: `{'action': 'pod-kill', 'mode': 'one', 'gracePeriod': 0}`

## stress-chaos

- Steady state confirmado após o ataque: True
- Máximo de réplicas observado: 5
- Configuração do ataque: `{'workers': 2, 'load': 100, 'duration': '120s'}`
