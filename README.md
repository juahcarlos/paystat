# PayStat — an AI analytics agent for payment data in ClickHouse

A microservice built on **FastAPI**, **LangChain** and **ClickHouse**. It accepts business questions in natural language, explores the data schema on its own, runs safe read-only queries, and returns structured findings (metrics, insights, limitations) without exposing SQL or the internals of the database.

---

## 1. Features

* **Autonomous schema discovery.** The agent does not guess field names: before querying, it reads the real schema of `analytics.transactions` through ClickHouse system tables.
* **Multi-step analysis.** The result of each query guides the next step. Limit per the spec: up to 3 LLM calls per analysis (`MAX_AGENT_ITERATIONS`).
* **Multi-layer SQL protection:**
  * only `SELECT`, `WITH`, `DESCRIBE`, `EXPLAIN` are allowed;
  * DDL/DML and service operations are forbidden (`DROP`, `INSERT`, `ALTER`, `TRUNCATE`, `GRANT`, etc.);
  * functions that access external resources are forbidden (`url()`, `file()`, `s3()`, `remote()`, etc.);
  * access only to tables from the allowlist (`transactions`, `analytics.transactions`);
  * forced `LIMIT` of at most 1000 rows;
  * a dedicated ClickHouse user with read-only rights and server-side settings `readonly=1`, a timeout, and a result row limit.
* **SQL never reaches the API response.** Code blocks and `SELECT ... FROM ...` are stripped from all text fields of the response.
* **LLM fault tolerance.** First `openai/gpt-oss-120b` on the free GroqCloud tier; on an error or an exhausted limit it switches to `llama-3.3-70b-versatile`, then to `llama-3.1-8b-instant`. If all providers are unavailable, the API responds with a controlled 503 error.
* **Frontend readiness.** CORS is configured for local origins (Vite/React).

---

## 2. Data

All amounts in the database are in **US dollars (USD)**; there is no separate currency field.

Database `analytics`, table `transactions` (MergeTree, `ORDER BY (created, id)`):

| Column | Type | Meaning |
| :--- | :--- | :--- |
| `id` | `UInt64` | Transaction identifier |
| `tariff_id` | `UInt32` | Tariff identifier (115, 116, 0 in the seeds) |
| `amount` | `Decimal64(2)` | Amount in USD |
| `email` | `String` | Customer email (anonymized, domain `a.invalid`) |
| `expires` | `DateTime` | Expiration time |
| `created` | `DateTime` | Transaction creation time |
| `complete` | `UInt8` | `1` — payment completed, `0` — not completed |
| `refund` | `Nullable(UInt8)` | Refund flag (`0` or `NULL` — no refund) |
| `system` | `Nullable(String)` | Payment system: `crypto` or `freekassa` |

Test data (about 140 rows for 2023 and 2026) is located in `docker/clickhouse/transactions.sql` and is loaded on the **first** ClickHouse start with an empty volume. A random half of the rows have `complete = 1`.

---

## 3. Project structure

```text
/opt/llm/paystat/
├── PRD.md                       # Technical specification (immutable)
├── README.md                    # Documentation
├── CLAUDE.md                    # Context for AI assistants
├── pyproject.toml               # Dependencies (uv), Ruff, mypy, pytest
├── uv.lock                      # Pinned dependency versions
├── Dockerfile                   # Python 3.13-slim, installation via uv
├── docker-compose.yml           # app service and ClickHouse
├── clear.sh                     # Removes caches (.pytest_cache, .mypy_cache, __pycache__)
├── .env.example                 # Example environment variables
├── docker/
│   └── clickhouse/
│       ├── init.sh              # Substitutes DB, user and password from .env into the SQL template
│       └── transactions.sql     # Init template: DB, table, read-only user, seeds
├── src/paystat/
│   ├── main.py                  # FastAPI, CORS, global error handler
│   ├── config.py                # Settings (pydantic-settings)
│   ├── api/routes.py            # Endpoints /analyze and /health
│   ├── schemas/api.py           # Pydantic request and response schemas
│   ├── database/
│   │   ├── connection.py        # ClickHouse client with server-side restrictions
│   │   └── schema.py            # Schema introspection
│   ├── security/validator.py    # SQL validator
│   ├── providers/
│   │   ├── base.py              # Provider interface and exceptions
│   │   ├── groq_provider.py     # GroqCloud via langchain-groq
│   │   └── manager.py           # Provider switching on failures
│   └── agent/
│       ├── prompts.py           # System prompt
│       ├── tools.py             # Agent tools: inspect_schema, execute_analytics_query
│       └── analyst.py           # Reasoning loop and final JSON parsing
└── tests/                       # Unit and integration tests
```

---

## 4. Environment variables (`.env`)

Create `.env` from the example:

```bash
cp .env.example .env
```

Be sure to set your own `GROQ_API_KEY` (no spaces or quotes). If the key is empty, `/analyze` responds with 503: the logs will show "Provider list is empty".

| Variable | Description | Default |
| :--- | :--- | :--- |
| `APP_ENV` | Run mode | `development` |
| `APP_HOST` | API address | `0.0.0.0` |
| `APP_PORT` | API port | `8001` |
| `CLICKHOUSE_HOST` | ClickHouse host when running from the host machine (inside the compose docker network, `clickhouse` is substituted) | `localhost` |
| `CLICKHOUSE_PORT` | ClickHouse HTTP port | `8123` |
| `CLICKHOUSE_NATIVE_PORT` | ClickHouse native port (for exposing outside) | `9000` |
| `CLICKHOUSE_DATABASE` | Analytics database | `analytics` |
| `CLICKHOUSE_USER` | Read-only user | `readonly_user` |
| `CLICKHOUSE_PASSWORD` | Password of this user | `readonly_secret` |
| `GROQ_API_KEY` | Free GroqCloud key | empty |
| `PRIMARY_MODEL` | Primary model | `openai/gpt-oss-120b` |
| `FALLBACK_MODELS` | Fallback models, comma-separated | `llama-3.3-70b-versatile,llama-3.1-8b-instant` |
| `MAX_AGENT_ITERATIONS` | Limit of LLM calls per analysis | `3` |
| `MAX_QUERY_ROWS` | Maximum rows in a query result | `1000` |
| `QUERY_TIMEOUT_SECONDS` | ClickHouse query timeout | `10` |
| `CORS_ALLOWED_ORIGINS` | Allowed origins, comma-separated | `http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000` |

### GroqCloud key

The key is issued for free at [console.groq.com](https://console.groq.com) in the API Keys section. You do not need to choose a paid plan: the account stays on the free tier until you upgrade to a paid one yourself. Free-tier limits apply to the whole organization and depend on the model. For `openai/gpt-oss-120b` the approximate figures are: 30 requests per minute, 1000 per day, 8000 tokens per minute, 200,000 per day. See the exact values on the limits page in the Groq console.

---

## 5. Running in Docker

```bash
docker compose up --build
```

This command builds the image and starts ClickHouse with the table and test data. The code from `src/` is copied into the image, so after changing it a rebuild is required (`--build`). To run in the background add `-d`, and view logs like this:

```bash
docker compose logs -f app
```

Available after startup:

* **Swagger UI:** http://localhost:8001/docs
* **ReDoc:** http://localhost:8001/redoc
* **Healthcheck:** http://localhost:8001/health

In `docker-compose.yml` the built-in healthcheck for the `app` service is disabled so that it does not clutter the logs with `/health` requests every 10 seconds. The `/health` endpoint itself works.

Stopping:

```bash
docker compose down
```

### Reseeding data

The SQL from `transactions.sql` runs only on the first start with an empty volume. To change the data or schema, remove the volume together with the containers and start again:

```bash
docker compose down -v && docker compose up
```

In `transactions.sql` the placeholders `__DB__`, `__RO_USER__` and `__RO_PASSWORD__` are substituted by the `init.sh` script from `.env`. Do not replace them manually, and when copying the file make sure your editor or messenger does not eat the underscores or turn quotes into curly ones.

---

## 6. API usage

### Health check (`GET /health`)

```bash
curl http://localhost:8001/health
```

```json
{
  "status": "ok",
  "database_connected": true,
  "version": "0.1.0"
}
```

If ClickHouse is unavailable, `status` will be `degraded` and `database_connected` will be `false`.

### Analytics request (`POST /analyze`)

Request body: the `question` field, a string of 3 to 1000 characters.

```bash
curl -X POST "http://localhost:8001/analyze" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the total revenue and volume of completed payments for September 2026?"}'
```

Example response on real test data:

```json
{
  "question": "What is the total revenue and volume of completed payments for September 2026?",
  "summary": "In September 2026 the system processed 12 completed payments, with total revenue of 36 USD.",
  "metrics": [
    {
      "name": "Total revenue of completed payments",
      "value": 36,
      "unit": "USD",
      "description": "Sum of all payments with status complete = 1 and no refund (refund = NULL or 0) for the period 01-09-2026 – 30-09-2026."
    },
    {
      "name": "Volume of completed payments",
      "value": 12,
      "unit": "pcs",
      "description": "Number of records meeting the same conditions."
    },
    {
      "name": "Average ticket of completed payments",
      "value": 3,
      "unit": "USD",
      "description": "Average amount of one completed payment (revenue ÷ volume)."
    }
  ],
  "insights": [
    "The average ticket in September 2026 was 3 USD."
  ],
  "limitations": [
    "The data is limited to one month (September 2026); comparison with other periods requires additional queries."
  ],
  "steps_count": 3
}
```

The model's response is non-deterministic: wording changes from request to request, but the numbers must match the data.

### Response codes

| Code | When |
| :--- | :--- |
| `200` | Analysis completed |
| `422` | Invalid request body (for example, an empty or too short `question`) |
| `500` | Unexpected error, details only in the server logs |
| `503` | All LLM providers are unavailable, have exhausted their limits, or `GROQ_API_KEY` is not set |

### Provider switching

The provider manager stores the index of the active model. On a call error (limit, network, authorization) it tries the next model in a circular order and remembers the one that responded. If none responds, `AllProvidersExhaustedError` is raised and the API returns 503 without technical details.

---

## 7. Checks and tests

Unit tests do not require paid APIs: the LLM is replaced with controlled mocks. Integration tests (marker `integration`) use a real ClickHouse and are skipped if it is unavailable.

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Integration tests read the connection from `.env`, so they require a running ClickHouse (`CLICKHOUSE_HOST=localhost`).

---

## 8. Known limitations

* **Groq free-tier limits.** During intensive testing the limits are used up quickly; in that case the service responds with 503.