# Xiaomi Mi Camera 2K Magnetic Mount (isa.camera.hlc7) — Home Assistant, local, sem go2rtc

Integração custom para a câmera Xiaomi 2K Magnetic Mount (versão chinesa,
modelo `isa.camera.hlc7`). Esse modelo não tem RTSP, ONVIF nem HTTP. A
integração fala os protocolos nativos da câmera direto do Home Assistant:

- **miIO** (UDP 54321): controles e estados, com o token da câmera;
- **MISS sobre CS2 P2P** (UDP 32108, ou TCP se a câmera pedir): vídeo
  H.265 + áudio PCMA e alto-falante, criptografados com ChaCha20.

O vídeo é entregue ao HA por um servidor RTSP interno (`127.0.0.1`, porta
aleatória), sem recodificar. O HA usa esse RTSP para o WebRTC (via o go2rtc
embutido do próprio HA Core) e para snapshots.

**Nuvem Xiaomi:** usada no login (uma vez), para listar as câmeras (IP e
token atualizados a cada início) e para assinar cada sessão de vídeo
(`miss_get_vendor`, ~1,5 s). Vídeo, áudio e controles trafegam só na LAN. A
sessão P2P só abre quando alguém assiste ou o alto-falante é usado, e fecha
30 s depois.

O código P2P/cloud é um porte para Python da fonte `xiaomi` do
[go2rtc](https://github.com/AlexxIT/go2rtc) (MIT, © AlexxIT).

## Entidades (por câmera)

| Tipo | Entidade | MIoT |
|---|---|---|
| camera | vídeo ao vivo + snapshot | — |
| media_player | Alto-falante (TTS/arquivos) | — |
| switch | Câmera ligada / Detecção de movimento / Luz indicadora / Marca d'água | 2.1 / 5.1 / 3.1 / 2.4 |
| select | Visão noturna / Modo de gravação / Rotação / Sensibilidade | 2.3 / 2.7 / 2.2 / 5.3 |
| number | Intervalo entre alertas (1–30 min) | 5.2 |
| sensor | Cartão SD: status, total, livre, usado | 4.1–4.4 |
| button | Reiniciar | ação 6.1 |

Ainda não tem: sensor de movimento/pessoa (a câmera manda esses eventos só
para a nuvem Xiaomi) e microfone do navegador para a câmera.

## Instalação

HACS → ⋮ → *Repositórios personalizados* → este repositório (Integração) →
instalar → reiniciar o HA → *Adicionar integração* → "Xiaomi Camera 2K".

1. Conta Mi Home (e-mail/telefone/ID) e senha, região `cn` para a versão
   chinesa. A senha não é guardada, só um token de sessão.
2. Se a Xiaomi pedir, digite o código enviado por e-mail/SMS ou o captcha.
3. Escolha as câmeras.

Opções: qualidade do vídeo (HD/SD/auto).

## Alto-falante

```yaml
action: tts.speak
target: {entity_id: tts.google_translate_pt_br}
data:
  media_player_entity_id: media_player.camara_mi_2k_lavanderia_alto_falante
  message: "Olá!"
```

O volume do media player é um ganho de software (0,5 = original, 1,0 = 2x).

## Frigate / outros NVRs (opcional)

Em *Configurar* da integração, ligue **Expor RTSP na rede local**, escolha
porta (padrão 8556, para não colidir com o go2rtc do Frigate em 8554/8555),
usuário e senha. Cada câmera fica em
`rtsp://usuário:senha@<IP do HA>:8556/<nome_da_câmera>` (o endereço exato
aparece no atributo `rtsp_lan_url` da entidade câmera). Só TCP, sem
recodificar (H.265 + PCMA).

```yaml
# Frigate
go2rtc:
  streams:
    lavanderia:
      - rtsp://frigate:SENHA@192.168.80.200:8556/camara_mi_2k_lavanderia
cameras:
  lavanderia:
    ffmpeg:
      inputs:
        - path: rtsp://127.0.0.1:8554/lavanderia
          input_args: preset-rtsp-restream
          roles: [record, detect]
      output_args:
        record: preset-record-generic-audio-aac
    detect:
      width: 1280
      height: 720
      fps: 5
```

## Latência

O vídeo é H.265 sem recodificação. Safari toca direto via WebRTC. Chrome e o
app Android em geral não decodificam H.265 em WebRTC; nesses, o HA cai para
HLS (mais atraso).

## Teste fora do HA

`custom_components/xiaomi_hlc7/hlc7_test.py` testa login, P2P e alto-falante
num `python3` comum (precisa de `cryptography`). Rode numa máquina na mesma
rede **sem NAT** (o add-on Terminal do HA fica atrás de NAT e perde as
respostas UDP da câmera).

## Licença

MIT
