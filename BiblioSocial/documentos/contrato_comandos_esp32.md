# Contrato HTTPS de comandos da ESP32

Este documento descreve a integração esperada do firmware externo. O firmware
não está neste repositório e deve ser alterado e validado no projeto PlatformIO
em que já é mantido.

## Configuração

- Backend: `https://projeto-bibliosocial.up.railway.app`
- Identificador padrão: `esp32-principal`
- Autenticação: cabeçalho `X-Chave-Dispositivo` com o mesmo valor da variável
  `CHAVE_DISPOSITIVO` do Railway.
- A chave deve existir somente na configuração local do firmware. Nunca deve ser
  colocada no frontend, neste documento ou em arquivo versionado.
- O cliente TLS deve validar o hostname e a cadeia do certificado. Não usar
  `setInsecure()`.

## Polling

A cada aproximadamente 2 segundos, sem impedir o loop de RFID e ultrassom:

```http
GET /api/dispositivo/comandos/proximo?dispositivo=esp32-principal
X-Chave-Dispositivo: <chave configurada localmente>
```

Sem comando, a resposta é:

```json
{"comando": null, "intervalo_ms": 2000, "servidor_em": 1794850000}
```

Com comando:

```json
{
  "comando": {
    "id": "identificador-unico",
    "tipo": "led_verde",
    "expira_em": 1794850045
  },
  "intervalo_ms": 2000,
  "servidor_em": 1794850000
}
```

O dispositivo deve ignorar um comando cujo `expira_em` já passou. Os únicos
tipos permitidos e seus efeitos são:

| Tipo | Efeito esperado |
|---|---|
| `led_verde` | LED verde por 2 segundos |
| `led_vermelho` | LED vermelho por 2 segundos |
| `buzzer` | dois bipes curtos |
| `sinal_sucesso` | LED verde e dois bipes agudos |
| `sinal_erro` | LED vermelho e dois bipes graves |

## Confirmação e idempotência

Após a execução:

```http
POST /api/dispositivo/comandos/identificador-unico/confirmar
X-Chave-Dispositivo: <chave configurada localmente>
Content-Type: application/json

{
  "dispositivo": "esp32-principal",
  "resultado": "executado",
  "detalhe": "LED acionado por 2 segundos"
}
```

Em caso de erro, usar `resultado: "falhou"` e uma descrição curta em `detalhe`.
A confirmação é idempotente: repeti-la não muda um resultado terminal.

Enquanto não houver confirmação, o servidor devolve o mesmo comando. Para não
acionar a saída duas vezes se a confirmação se perder, o firmware deve salvar o
ID antes de executar e manter de forma persistente o último ID e resultado. Ao
receber novamente um ID já concluído, deve somente reenviar a confirmação.

## Requisitos de execução no firmware externo

- Manter exatamente a pinagem já definida no `include/pinos.h` externo: RC522
  SS 5, SCK 18, MOSI 23, MISO 19, RST 22; HC-SR04 TRIG 33 e ECHO 34; LED verde
  25, LED vermelho 27 e buzzer 32. Não usar LED amarelo.
- Implementar LEDs/bipes com máquina de estados baseada em `millis()`, sem
  `delay()` longo.
- Executar HTTPS fora do caminho crítico do loop (por tarefa FreeRTOS ou outra
  arquitetura assíncrona já existente) e configurar timeouts curtos.
- Se Wi-Fi ou Railway estiver indisponível, continuar RFID, HC-SR04 e os sinais
  locais; apenas tentar novamente no ciclo seguinte.
- Preservar `evento_id` único no endpoint de leitura já existente.

Nenhum teste automatizado do Flask comprova os GPIOs. A integração só pode ser
considerada validada depois de compilar o projeto PlatformIO externo, gravar a
ESP32 e observar cada saída física.
