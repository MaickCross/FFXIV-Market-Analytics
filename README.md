<div align="center">

# 🏰 FFXIV Market Analytics

**Pipeline de análise de mercado para Final Fantasy XIV**

Coleta, processa e visualiza dados do market board do servidor Behemoth em tempo real para identificar os itens com maior rotatividade financeira.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![Dash](https://img.shields.io/badge/Dash-2.17-008DE4?style=flat&logo=plotly&logoColor=white)](https://dash.plotly.com/)

![Dashboard Preview](https://img.shields.io/badge/status-active-00bc8c?style=flat)

</div>

---

## 📋 Índice

- [Sobre o projeto](#-sobre-o-projeto)
- [Funcionalidades](#-funcionalidades)
- [Arquitetura](#-arquitetura)
- [Tecnologias](#-tecnologias)
- [Rodando localmente](#-rodando-localmente)
- [Estrutura de pastas](#-estrutura-de-pastas)
- [Variáveis de ambiente](#-variáveis-de-ambiente)
- [Scripts disponíveis](#-scripts-disponíveis)
- [Licença](#-licença)

---

## 🎯 Sobre o projeto

O **FFXIV Market Analytics** é uma aplicação ETL de análise do mercado interno do jogo *Final Fantasy XIV*.

O market board do FFXIV é um mercado livre onde jogadores compram e vendem itens entre si. Com milhares de itens disponíveis e preços definidos pela própria comunidade, identificar quais itens geram mais lucro com mais rapidez é uma tarefa difícil de fazer manualmente.

Este projeto automatiza esse processo inteiramente:

- A cada hora, coleta dados de preços e vendas da API pública [Universalis](https://universalis.app/), que agrega informações do mercado enviadas por jogadores
- Processa e armazena o histórico em um banco de dados local com retenção de 60 dias
- Detecta automaticamente vendas realizadas pelos retainers (vendedores) do jogador
- Exibe um dashboard interativo com ranking de rotatividade, tendências de preço e desempenho dos seus retainers

> **Nota:** esta aplicação não acessa o jogo diretamente, não realiza ações automatizadas e não requer nenhuma conta ou autenticação com a Square Enix. Utiliza exclusivamente APIs públicas mantidas pela comunidade.

---

## ✨ Funcionalidades

**Dashboard de Mercado**
- Ranking de itens por rotatividade (gil/dia) filtrado por categoria e período
- Cards de KPI: itens ativos, total de vendas e volume financeiro
- Gráfico de tendência de preço NQ/HQ ao selecionar um item
- Filtros por categoria (Armor, Housing, Items, Main Arm/Off Arm) e período (7, 14 ou 30 dias)

**Monitoramento de Retainers**
- Listagem em tempo real dos itens ativos dos seus retainers
- Histórico de vendas inferido automaticamente pela variação entre coletas
- KPIs de total ganho, vendas realizadas e listagens ativas

**Pipeline ETL automático**
- Coleta assíncrona de ~16.000 itens em paralelo com controle de concorrência
- Deduplicação automática via UNIQUE CONSTRAINTs no banco
- Retenção configurável com purge automático de dados antigos

---

## 🏗️ Arquitetura

```
Universalis API
      │
      ▼
  extractor.py     ← busca dados brutos via HTTP assíncrono (httpx)
      │
      ▼
  transformer.py   ← processa, limpa e detecta vendas de retainers
      │
      ▼
  loader.py        ← persiste no PostgreSQL com deduplicação
      │
      ▼
  PostgreSQL       ← armazena 60 dias de histórico
      │
      ▼
  dashboard/       ← interface web interativa (Dash + Plotly)
```

O pipeline é orquestrado pelo `scheduler.py` e executado automaticamente a cada hora via APScheduler.

---

## 🛠️ Tecnologias

| Camada | Tecnologia | Versão |
|---|---|---|
| Linguagem | Python | 3.12 |
| Banco de dados | PostgreSQL | 15 |
| Container | Docker + Compose | — |
| HTTP assíncrono | httpx | 0.27 |
| ORM + Migrations | SQLAlchemy + Alembic | 2.0 / 1.13 |
| Agendamento | APScheduler | 3.10 |
| Dashboard | Dash + Plotly | 2.17 / 5.22 |
| Visualização | Dash Bootstrap Components | 1.6 |
| Dados tabulares | pandas | 2.2 |
| Configuração | pydantic-settings | 2.3 |

---

## 🚀 Rodando localmente

### Pré-requisitos

Antes de começar, certifique-se de ter instalado na sua máquina:

- [Python 3.12](https://www.python.org/downloads/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Git](https://git-scm.com/)

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/ffxiv-market-analytics.git
cd ffxiv-market-analytics
```

### 2. Crie o ambiente virtual e instale as dependências

```bash
# Cria o ambiente virtual
python -3.12 -m venv venv

# Ativa o ambiente virtual
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Instala as bibliotecas e dependências
pip install -r requirements.txt
```

### 3. Configure as variáveis de ambiente

Copie o arquivo de exemplo e edite com suas configurações:

```bash
cp .env.example .env
```

Abra o `.env` e altere os campos marcados — veja a seção [Variáveis de ambiente](#-variáveis-de-ambiente) para detalhes.

### 4. Suba o banco de dados

```bash
docker compose up -d
```

Aguarde o container iniciar. Você pode verificar com:

```bash
docker compose logs postgres
# aguarda a mensagem: "database system is ready to accept connections"
```

### 5. Crie as tabelas com Alembic

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### 6. Popule o catálogo de itens

Este passo busca todos os itens vendáveis do FFXIV nas APIs externas e os insere no banco. Executado **uma única vez**.

```bash
python scripts/seed_items.py
```

O processo leva cerca de 1-2 minutos. Você verá o progresso no terminal:

```
14:32:01 [INFO] Buscando IDs de itens negociáveis na Universalis...
14:32:02 [INFO]   → 16,794 itens encontrados.
14:32:02 [INFO] Buscando metadados em 168 lotes no XIVAPI v2...
14:33:01 [INFO] ✓ Seed concluído. Total de itens no banco: 16,571
```

### 7. Cadastre seus retainers

Para monitorar suas próprias vendas, insira seus retainers no banco. Substitua pelos nomes exatos dos seus retainers no jogo:

```bash
docker exec -i ffxiv_market_db psql -U ffxiv -d ffxiv_market -c \
  "INSERT INTO my_retainers (name, owner_character, notes) VALUES ('NomeDoRetainer', 'NomeDoPersonagem', 'retainer principal');"
```

### 8. Inicie o ETL (terminal 1)

```bash
python src/scheduler.py
```

A primeira coleta inicia imediatamente. O ETL repetirá a cada hora automaticamente.

```
14:35:00 [INFO] Servidor: Behemoth
14:35:00 [INFO] Intervalo de coleta: 1h
14:35:00 [INFO] Executando primeira coleta imediatamente...
14:35:00 [INFO] ═══════════════════════════════════════
14:35:00 [INFO]   ETL iniciado: 2026-06-14 17:35:00 UTC
```

### 9. Abra o dashboard (terminal 2)

```bash
python src/dashboard/app.py
```

Acesse no browser: **http://localhost:8050**

---

## 📁 Estrutura de pastas

```
ffxiv-market-analytics/
│
├── src/
│   ├── config.py              # configurações centralizadas via .env
│   ├── scheduler.py           # orquestrador do pipeline ETL
│   │
│   ├── db/
│   │   ├── models.py          # modelos SQLAlchemy (tabelas)
│   │   └── session.py         # engine e factory de sessões
│   │
│   ├── etl/
│   │   ├── extractor.py       # coleta assíncrona da Universalis API
│   │   ├── transformer.py     # processamento e lógica de negócio
│   │   └── loader.py          # persistência no banco
│   │
│   └── dashboard/
│       ├── queries.py         # queries SQL para o dashboard
│       └── app.py             # interface Dash + Plotly
│
├── scripts/
│   ├── seed_items.py          # popula catálogo de itens (executar 1x)
│   └── purge.py               # remove dados antigos
│
├── alembic/
│   ├── env.py                 # configuração das migrations
│   ├── script.py.mako         # template de migration
│   └── versions/              # migrations geradas
│
├── docker-compose.yml         # PostgreSQL containerizado
├── requirements.txt           # dependências Python
├── alembic.ini                # configuração do Alembic
├── .env.example               # template de configuração
└── README.md
```

---

## ⚙️ Variáveis de ambiente

Copie `.env.example` para `.env` e configure os campos abaixo.

| Variável | Obrigatório | Descrição |
|---|---|---|
| `POSTGRES_PASSWORD` | ✅ | Senha do banco — escolha qualquer valor |
| `DATABASE_URL` | ✅ | String de conexão — deve conter a mesma senha acima |
| `POSTGRES_USER` | — | Usuário do banco (padrão: `ffxiv`) |
| `POSTGRES_DB` | — | Nome do banco (padrão: `ffxiv_market`) |
| `WORLD` | — | Servidor do FFXIV (padrão: `Behemoth`) |
| `ETL_INTERVAL_HOURS` | — | Intervalo de coleta em horas (padrão: `1`) |
| `RETENTION_DAYS` | — | Dias de histórico a manter (padrão: `60`) |
| `API_CONCURRENCY` | — | Requisições paralelas à API (padrão: `5`) |

> ⚠️ `POSTGRES_PASSWORD` e a senha dentro de `DATABASE_URL` devem ser **idênticas**.

---

## 📜 Scripts disponíveis

| Comando | Descrição |
|---|---|
| `python scripts/seed_items.py` | Popula o catálogo de itens (executar uma vez) |
| `python scripts/purge.py` | Remove dados com mais de `RETENTION_DAYS` dias |
| `python scripts/purge.py --days 30` | Purge com retenção personalizada |
| `python src/scheduler.py` | Inicia o pipeline ETL automático |
| `python src/dashboard/app.py` | Inicia o dashboard em http://localhost:8050 |
| `alembic upgrade head` | Aplica migrations pendentes |
| `alembic revision --autogenerate -m "msg"` | Gera nova migration |
| `docker compose up -d` | Sobe o banco de dados |
| `docker compose stop` | Para o banco (dados preservados) |

---

## 📄 Licença

Os dados são obtidos de APIs públicas mantidas pela comunidade ([Universalis](https://universalis.app/) e [XIVAPI](https://v2.xivapi.com/)).

## 👨‍💻 Autor

Projeto desenvolvido por [@MaickCross](https://github.com/MaickCross)