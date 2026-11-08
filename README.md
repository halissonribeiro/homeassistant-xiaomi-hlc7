# Xiaomi Mi Camera 2K Magnetic Mount (isa.camera.hlc7) — Home Assistant, 100% local

Integração custom para a câmera Xiaomi 2K Magnetic Mount (versão chinesa,
modelo `isa.camera.hlc7`). Esse modelo não tem RTSP, ONVIF nem HTTP. Ele só fala:

- **miIO** (UDP 54321): controles e estados, usando o token da câmera;
- **P2P CS2** (UDP): vídeo H.265 + áudio PCMA nos dois sentidos, lido pelo
  [go2rtc](https://go2rtc.org/internal/xiaomi/) (fonte `xiaomi`, v1.9.13+).

A nuvem Xiaomi só é usada para buscar as chaves do vídeo ao abrir a conexão
(no go2rtc) e para extrair o token uma vez. Vídeo e controles trafegam na LAN.

## Entidades

| Tipo | Entidade | MIoT |
|---|---|---|
| camera | vídeo ao vivo (RTSP do go2rtc, WebRTC no HA) + snapshot | — |
| media_player | Alto-falante (TTS/arquivos via go2rtc) | — |
| switch | Câmera ligada | 2.1 |
| switch | Detecção de movimento | 5.1 |
| switch | Luz indicadora | 3.1 |
| switch | Marca d'água de horário | 2.4 |
| select | Visão noturna (desligada/ligada/auto) | 2.3 |
| select | Modo de gravação (contínua/movimento/parada) | 2.7 |
| select | Rotação da imagem (0°/180°) | 2.2 |
| select | Sensibilidade de movimento | 5.3 |
| number | Intervalo entre alertas (1–30 min) | 5.2 |
| sensor | Cartão SD: status, total, livre, usado | 4.1–4.4 |
| button | Reiniciar | ação 6.1 |

Ainda não tem: sensor de movimento/pessoa. A câmera manda esses eventos só
para a nuvem Xiaomi, e eles ficam para uma próxima versão.

## Pré-requisitos

1. **go2rtc** (add-on de `https://github.com/AlexxIT/hassio-addons`) com a
   câmera adicionada em *Add → Xiaomi* (login da conta Mi, região `cn`).
   Anote o **nome do stream**.
2. **Token miIO** da câmera: rode o
   [Xiaomi Cloud Tokens Extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
   com o servidor `cn` e copie o token (32 caracteres) do `isa.camera.hlc7`.

## Instalação

HACS → menu ⋮ → *Repositórios personalizados* → este repositório (categoria
Integração) → instalar → reiniciar o HA → *Adicionar integração* →
"Xiaomi Camera 2K Magnetic Mount (local)".

Campos: IP da câmera, token, URL da API do go2rtc (padrão
`http://127.0.0.1:1984`) e nome exato do stream.

## Áudio bidirecional

- **Falar pelo navegador/celular:** use o card
  [WebRTC Camera](https://github.com/AlexxIT/WebRTC) apontando para o stream
  do go2rtc com o microfone:

  ```yaml
  type: custom:webrtc-camera
  url: "Câmara Mi 2K (Lavanderia)"
  media: video,audio,microphone
  ```

  O microfone do navegador só funciona com o HA em **HTTPS**.
- **Avisos/TTS por automação:** use o media player "Alto-falante":

  ```yaml
  action: tts.speak
  target: {entity_id: tts.google_translate_pt_br}
  data:
    media_player_entity_id: media_player.camera_xiaomi_alto_falante
    message: "Olá!"
  ```

## Latência

O vídeo é H.265. Safari toca direto por WebRTC (sem recodificar, menor delay).
Chrome e o app Android geralmente não decodificam H.265 em WebRTC. Nesses,
crie no go2rtc um stream extra com `ffmpeg:<stream>#video=h264` (custa CPU e um
pouco de delay).

## Licença

MIT
