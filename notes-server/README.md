# Servidor de notas da Fazenda

Recebe as ideias que você escreve no terreno de nuvens e guarda cada uma como um arquivo `.md` na pasta `notas/`.

## Subir no homeserver

```bash
cd notes-server
cp .env.example .env
# edite o .env e troque o token (openssl rand -hex 24)
docker compose up -d --build
```

As ideias ficam em `notes-server/notas/*.md`. Cada arquivo tem:

```
---
title: Bot de lembretes
updated: 2026-10-24T12:00:00.000Z
---
texto da ideia...
```

Faça backup dessa pasta como qualquer outra (ou sincronize com Syncthing).

## Deixar acessível pelo iPhone (HTTPS)

O app roda em HTTPS (GitHub Pages), então o servidor também precisa responder em HTTPS. O jeito mais simples é o Tailscale:

```bash
tailscale serve --bg 8787
```

Ele mostra um endereço como `https://meuservidor.tailnet.ts.net`. Esse é o endereço que você coloca no app. O iPhone precisa estar com o Tailscale ligado.

## Ligar no app

1. Toque no `＋` do terreno de nuvens.
2. Toque em ⚙️ e preencha a **URL** (acima) e o **token** (o do `.env`).
3. Toque em **Testar conexão**. Se der ✅, está pronto: as ideias passam a sincronizar entre aparelhos.

Sem servidor configurado, as ideias ficam só no aparelho (e sincronizam quando você configurar).

## API (resumo)

Todas as rotas, menos `/health`, pedem `Authorization: Bearer <token>`.

| Método | Rota | O que faz |
|---|---|---|
| GET | `/health` | testa se está no ar |
| GET | `/notes` | lista todas as ideias |
| GET | `/notes/<id>` | uma ideia |
| PUT | `/notes/<id>` | cria ou atualiza `{title, text, updated}` |
| DELETE | `/notes/<id>` | apaga |

## Segurança

- O token fica só no seu aparelho (localStorage) e no `.env` do servidor.
- O CORS só aceita o endereço do app (`ALLOWED_ORIGIN`).
- A porta só escuta em `127.0.0.1`; quem expõe é o Tailscale, só para a sua rede.
