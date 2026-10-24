# Servidor da Fazenda (notas + status ao vivo)

Este servidor faz duas coisas para o app da fazenda:

1. **Notas:** guarda as ideias que você escreve nos terrenos com névoa como arquivos `.md`.
2. **Status ao vivo:** informa CPU, RAM, disco e rede do servidor e a lista de containers Docker, para a fazenda deixar de ser uma simulação.

## Subir no homeserver

```bash
cd notes-server
cp .env.example .env
# edite o .env e troque o token (openssl rand -hex 24)
docker compose up -d --build
```

Sobem dois containers:

- `fazenda-notas`: este servidor (escuta só em `127.0.0.1:8787`).
- `fazenda-docker-proxy`: deixa o servidor **ler** os containers sem acesso ao Docker inteiro (somente leitura, só em `127.0.0.1:2375`).

Os dois ficam escondidos na fazenda (não viram bichinhos).

## Deixar acessível pelo iPhone (HTTPS)

O app roda em HTTPS (GitHub Pages), então o servidor também precisa responder em HTTPS. O jeito mais simples é o Tailscale:

```bash
tailscale serve --bg 8787
```

Ele mostra um endereço como `https://meuservidor.tailnet.ts.net`. Esse é o endereço que você coloca no app. O aparelho precisa estar com o Tailscale ligado.

## Ligar no app

1. Toque em ⚙️ no painel de status (canto superior esquerdo).
2. Preencha a **URL** (acima) e o **token** (o do `.env`).
3. Toque em **Testar conexão**. Se der ✅, o painel passa a mostrar `🟢 ao vivo`.

Sem servidor configurado, o app funciona em **modo demonstração** (dados simulados).

## Como a fazenda mostra seus containers

Cada container vira um bichinho, e o prédio dele depende do tipo:

| Prédio | Bichinho | Containers (pelo nome) |
|---|---|---|
| Casinha do cachorro (DNS) | cachorro | `adguard`, `pihole`, `unbound`... |
| Moinho (monitoramento) | — | `kuma`, `uptime`, `grafana`, `prometheus`, `netdata`... |
| Cofre (segurança) | ovelhas | `vaultwarden`, `authelia`, `wireguard`... |
| Chiqueiro (bancos de dados) | porquinhos | `postgres`, `mysql`, `redis`, `mongo`... |
| Galinheiro (agentes) | galinhas | `agent`, `bot`, `worker`, `secretaria`, `alexa`... |
| Celeiro (apps) | vacas | todo o resto |

- Container **parado** ou **unhealthy**: o bichinho fica doente 🤒 e o prédio sinaliza.
- Para escolher o prédio de um container, ponha o rótulo `farm.group` no `docker-compose.yml` dele, com um destes valores: `dns`, `monitor`, `security`, `db`, `agent` ou `app`:

  ```yaml
  labels:
    farm.group: security
  ```

- Para esconder algum container, liste o nome em `HIDE_CONTAINERS` (separado por vírgula).

## Reiniciar pelo app (opcional, desligado)

Por padrão a fazenda **só observa**. Para reiniciar um container tocando nele, ponha `ENABLE_RESTART=1` no `.env` e rode `docker compose up -d` de novo.

Atenção: o docker-socket-proxy só aceita reinício com `POST=1`, e isso também permite a ele **criar containers**, o que na prática dá controle total do servidor para quem alcançar a porta `2375`. Ela só escuta em `127.0.0.1` e o servidor da fazenda exige o token, mas só ligue se aceitar esse risco.

## Gasto da secretária (o mesmo número do `/gasto`)

A secretária do Telegram guarda quantas chamadas e tokens usou no mês no banco dela (`data/secretaria.db`). O servidor da fazenda lê esse banco **só para leitura** e faz a mesma conta do `/gasto`: tokens × preço, contra o limite mensal. O painel mostra `🤖 Secretária US$ 1,10 de US$ 1,50`, em amarelo acima de 70% e vermelho no limite.

Para ligar, no `.env` do servidor da fazenda:

```
SECRETARIA_DATA=/home/SEU_USUARIO/secretaria/data
SECRETARIA_BUDGET_USD=1.5
SECRETARIA_PRICE_IN_PER_MTOK=1.0
SECRETARIA_PRICE_OUT_PER_MTOK=5.0
```

`SECRETARIA_DATA` é a pasta `data` da secretária, a mesma que o compose dela monta em `/data`. Os três valores seguintes devem ser **iguais** aos da secretária (`LLM_MONTHLY_BUDGET_USD`, `LLM_PRICE_IN_PER_MTOK` e `LLM_PRICE_OUT_PER_MTOK` no `.env` dela). Depois, `docker compose up -d`.

Isso mostra o que a **secretária** gastou no mês.

**Saldo estimado (opcional).** Como o banco guarda o gasto de todos os meses, dá para calcular `crédito comprado − gasto acumulado`. Acrescente no `.env`:

```
SECRETARIA_CREDIT_USD=5
SECRETARIA_CREDIT_FROM=2026-10
```

`SECRETARIA_CREDIT_USD` é quanto você comprou e `SECRETARIA_CREDIT_FROM` é o mês da compra (só o gasto desse mês em diante entra na conta). O painel passa a mostrar `💳 ~US$ 4,80 restantes`. Só é confiável se a secretária for a **única** a usar essa chave da API e se o preço configurado bater com o da Anthropic; qualquer outro uso (outros agentes, testes no Console) não entra na conta. Confira de vez em quando em **Gastos** no Console. O mês usa o horário do servidor, então na virada do mês pode haver diferença de algumas horas em relação ao Telegram.

## Saldo da API digitado à mão (sem chave de administrador)

Se a sua conta é individual, não há como ler o saldo automaticamente. Abra o app com `?config`, digite o saldo que você vê em **Gastos** no Console e toque em **Salvar saldo**. O painel mostra `💳 US$ 35,20 (em 24/10)`, com um aviso ⚠️ quando o valor tem mais de 7 dias.

## Crédito da API da Anthropic (opcional)

A Anthropic **não tem uma API que devolve o saldo restante**. O que existe é a [API de custos](https://platform.claude.com/docs/en/manage-claude/usage-cost-api), que informa quanto foi **gasto**. O servidor soma esse gasto desde a data da sua compra e o app mostra uma **estimativa**: `~US$ 35,00 restantes` (comprado − gasto).

Para ligar:

1. A conta precisa ser de **organização** (Console → Settings → Organization). A API de custos não existe para contas individuais.
2. Crie uma **chave de administrador** (Console → Settings → Admin keys). Ela começa com `sk-ant-admin`.
3. No `.env` do servidor, descomente e preencha:

   ```
   ANTHROPIC_ADMIN_KEY=sk-ant-admin01-...
   CREDIT_TOTAL_USD=50
   CREDIT_SINCE=2026-10-01
   ```

4. `docker compose up -d` de novo.

Cuidados:

- A chave de administrador tem **muito poder** sobre a organização (gerencia membros e chaves). Ela fica **só no `.env`** do servidor, nunca vai para o app nem para o GitHub. Deixe o arquivo só com você (`chmod 600 .env`) e revogue a chave no Console se achar que vazou.
- O valor é uma estimativa: a API de custos não inclui o Priority Tier, e não considera impostos, créditos que expiram nem bônus. Confira de vez em quando no Console (Billing).
- O servidor consulta a Anthropic no máximo a cada 5 minutos.

## Testes

```bash
python test_server.py
```

Sobe um Docker falso e confere status, logs, reinício, autenticação e notas.

## API (resumo)

Todas as rotas, menos `/health`, pedem `Authorization: Bearer <token>`.

| Método | Rota | O que faz |
|---|---|---|
| GET | `/health` | testa se está no ar |
| GET | `/status` | host (CPU, RAM, disco, rede, uptime) e containers |
| GET | `/credit` | gasto e saldo estimado da API da Anthropic (se configurado) |
| PUT | `/credit` | guarda o saldo digitado à mão `{remaining}` |
| GET | `/gasto` | gasto do mês da secretária (se configurado) |
| GET | `/containers/<nome>/logs?tail=25` | últimas linhas de log |
| POST | `/containers/<nome>/restart` | reinicia (só com `ENABLE_RESTART=1`) |
| GET | `/notes` | lista todas as ideias |
| GET | `/notes/<id>` | uma ideia |
| PUT | `/notes/<id>` | cria ou atualiza `{title, text, stage, updated}` (`stage`: `idea`, `build` ou `live`) |
| DELETE | `/notes/<id>` | apaga |

## Segurança

- O token fica só no seu aparelho (localStorage) e no `.env` do servidor.
- O CORS só aceita o endereço do app (`ALLOWED_ORIGIN`).
- O servidor só escuta em `127.0.0.1`; quem expõe é o Tailscale, só para a sua rede.
- O proxy do Docker é somente leitura e só escuta em `127.0.0.1`.
