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

## Terrenos com névoa (ideias de apps)

Os terrenos vizinhos da fazenda estão cobertos de névoa. No primeiro terreno bloqueado há um `+`: toque nele e escreva uma ideia de app para o servidor. Cada ideia plantada **desbloqueia um terreno** (são 6; depois o `+` fica ao lado da casa).

Cada terreno mostra a **fase** da ideia, que você muda na tela de ideias:

| Fase | No mapa |
|---|---|
| 💡 Ideia | um brotinho com uma placa |
| 🔨 Em obras | uma construção em andamento |
| ✅ No servidor | uma casinha pronta com bandeira |

Tocando num terreno liberado você abre a ideia dele. As ideias ficam no aparelho e, se você configurar o servidor de notas (⚙️ na tela de ideias), são guardadas como arquivos `.md` numa pasta do homeserver. Veja [`notes-server/`](notes-server/README.md).

## Servidor ao vivo

Com o servidor da fazenda ligado ([`notes-server/`](notes-server/README.md)), o app lê o seu homeserver de verdade:

- **CPU, RAM, disco e rede** do painel e da fazenda (horta = disco, lago = rede, fumaça da casa = CPU).
- **Cada container Docker vira uma casinha**, com um bichinho ao lado, dentro do cercadinho do tipo dele (apps, agentes, bancos de dados, DNS, segurança, monitoramento). O cercadinho cresce conforme o número de containers.
- Container parado ou não saudável deixa a casinha cinza e o bichinho doente 🤒. Tocando na casinha você vê CPU, memória e os **logs**, e o nome aparece ao dar zoom.
- O painel mostra `🟢 ao vivo`, `🔴 sem conexão` ou `🎮 demonstração`. Sem servidor configurado (⚙️), o app roda com dados simulados.
- Na versão ao vivo, as ações "Backup", "Atualizar" e "Limpar" ficam desativadas, porque ainda não fazem nada de verdade no servidor.

## Configuração do servidor

O endereço e o token do servidor são configurados uma vez e ficam escondidos. Para mudar, abra o app com `?config` no final do endereço (por exemplo `.../fazenda-homelab/?config`): aparece o ⚙️ para editar.

## Versão

A versão aparece no canto do painel de status (ex.: `v1.3.0 ↻`). Tocando nela o app apaga o cache e recarrega, buscando a versão mais nova.
