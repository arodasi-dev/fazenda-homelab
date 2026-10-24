# Fazenda Homelab

Seu homeserver como uma fazendinha em pixel art. Funciona no PC (teclado + mouse) e no iPhone/iPad (joystick, toque e pinça), instalável como app pela tela de início.

Hoje os dados (CPU, RAM, quedas dos serviços) são **simulados**.

## Publicar no GitHub Pages

1. Crie um repositório no GitHub (ex.: `fazenda-homelab`) e envie o conteúdo desta pasta para a branch `main`:

   ```bash
   git init
   git add .
   git commit -m "Fazenda Homelab"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/fazenda-homelab.git
   git push -u origin main
   ```

2. No GitHub: **Settings → Pages → Build and deployment → Source: Deploy from a branch → `main` / `(root)` → Save**.
3. Em ~1 minuto o app fica em `https://SEU_USUARIO.github.io/fazenda-homelab/`.

## Instalar no iPhone / iPad

1. Abra o link acima no **Safari** (precisa ser o Safari).
2. Toque em **Compartilhar** (quadrado com seta) → **Adicionar à Tela de Início**.
3. Abra pelo ícone: ele roda em tela cheia, como um app, e funciona offline.

## Controles

| | PC | iPhone/iPad |
|---|---|---|
| Andar | WASD / setas | joystick |
| Mover o mapa | arrastar | arrastar com um dedo |
| Zoom | roda do mouse, + / − | pinça, + / − |
| Selecionar bicho/prédio | clique | toque |
| Dia/noite | N | (modo automático pela hora) |

## Quando ligar ao servidor real

O GitHub Pages é **HTTPS**, e o navegador bloqueia chamadas de uma página HTTPS para `http://` (conteúdo misto). Para ler os dados do seu homeserver pelo iPhone, o servidor precisa responder em HTTPS, por exemplo com `tailscale serve` (certificado automático) e liberando CORS para o endereço do Pages.
