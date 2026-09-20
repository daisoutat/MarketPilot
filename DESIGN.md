# MarketPilot — Design Document

**Working name** : MarketPilot (renameable). Separate from the on-prem `Suivi_Importation` tracker.
**Mission** : Cloud-hosted web application for the **US and Canadian markets** combining Amazon **seller analytics** (own accounts) and **market/product research**, fed exclusively by **official Amazon APIs** (SP-API, PA-API) and kept fresh by **autonomous update systems**.
**Stack** : AWS. Python (FastAPI) + React SPA. Aurora PostgreSQL. IaC via AWS CDK (Python).

---

## 1. Goals & non-goals

### Goals
1. **Own-seller analytics (US + CA)** — orders, units, revenue, fees, FBA inventory, settlements, margins, per marketplace (`amazon.com`, `amazon.ca`).
2. **Market / product research (US + CA)** — product search by ASIN/keywords, price and Buy-Box snapshots over time, CAD ↔ USD FX normalization, cross-marketplace comparison.
3. **Autonomous update systems** — *all four*:
   - **Scheduled pipelines (ETL/cron)**: EventBridge Scheduler → Lambda sync jobs (orders, inventory, reports, price snapshots).
   - **Webhooks / push**: SP-API Notifications v2 → SQS → Lambda consumer.
   - **Self-repair / health**: DLQs, exponential backoff + jitter, health endpoints, CloudWatch alarms, automated recovery.
   - **Self-updating code**: CI/CD auto-deploy from git push (build → ECR → rolling update).
4. **Official APIs only**: Amazon SP-API and PA-API. No scraping.
5. **Multidimensional analysis** — drill-down from global categories → niches → product families → individual ASINs, with historic trend analysis.
6. **AI-assisted insights** — AI-driven demand forecasting, a data-grounded assistant (chatbot) with user customization, and compliance alerting.

### Non-goals (initial)
- No hosted selling (no integrate-your-Amazon-seller tools beyond our own app).
- No FBA inbound *shipment* editing; read-only analytics first.
- Forecasting stays **explainable**: statistical/rule-based models first (moving averages, seasonal decomposition), with ML models only once clean history exists.

---

## 2. Amazon API fundamentals (the ground rules)

### 2.1 Marketplaces
| Marketplace | Marketplace ID | Currency | SP-API region |
|---|---|---|---|
| United States | `ATVPDKIKX0DER` | USD | NA (`sellingpartnerapi-na.amazon.com`) |
| Canada | `A2VIGQ35RCS4UG` | CAD | NA |

### 2.2 SP-API authorization models
Amazon offers two authorization models; both must be supported behind an **auth adapter** (`SPAuthProvider`):

1. **Legacy – LWA + IAM role + STS** (`assume-role-with-web-identity`): Long-lived LWA refresh token per seller; swap for access token; SDK assumes your partner IAM role.
2. **New self-authorized key model** (current recommended for new apps): SP-API key (`ClientId`/`ClientSecret`) + dedicated IAM role, no refresh token. Token retrieval via the key-based endpoint, signed with temporary STS credentials from the role with web-identity.

Design decision: **implement the new key-based model first** (simpler onboarding, no OAuth consent per merchant), with the LWA flow as a configurable fallback.

### 2.3 Endpoints used (initial scope)
| Domain | Endpoint | Purpose |
|---|---|---|
| Orders v0 | `getOrders`, `getOrderItems` | Sales/units sync (US+CA) |
| Reports v2021-06-30 | `GET_MERCHANT_LISTINGS_ALL_DATA`, `GET_AMAZON_FEE_EVENTS`, (settlements) | Inventory, fees, settlements (async report flow) |
| Catalog v2022-04-01 | `searchCatalogItems`, `getCatalogItem` | Product metadata (market research) |
| Pricing v2022-05-01 | `getPricing`, `getListingOffers`, `getItemOffers` | Price/Buy-Box data |
| Product Fees v2022-01-01 | `getMyFeesEstimate` | Fee estimation for margin analysis |
| Notifications v1 | destinations + subscriptions → SQS | Push updates (orders, fee promotions) |
| PA-API 5.0 | `GetProducts`, `GetItems`, `ItemSearch` (via Associates) | Market research on non-owned ASINs |

### 2.4 Rate limits & throttling
- Every SP-API operation has a per-endpoint quota (requests/second + burst). Enforcement is **hard**: honor the recommended retry-after and the `x-amzn-RateLimit-*` / `x-amzn-RequestId` headers.
- Pipeline design treats throttling as normal: exponential backoff + full jitter, per-endpoint token buckets, and a **dead-letter queue** for anything that cannot complete.
- Reports flow mitigates burst pressure: `createReport` → poll `getReport` → `getReportDocument` → AES/GCM decrypt → transform → load.

### 2.5 PA-API 5.0 (market research)
- Requires Amazon Associates account; SigV4 signing; separate endpoints per country (`webservices.amazon.com`, `webservices.amazon.ca`).
- Used only for products **we do not necessarily sell**; SP-API catalog/pricing used for owned ASINs where grants allow.
- Snapshot only: no scraping, prices captured at scheduled cadence for history.

---

## 3. Architecture

```
                        ┌─────────────────────────────────────────────────┐
                        │                 AWS Cloud                        │
  User ── HTTPS ──► CloudFront ──► S3 (React SPA)                         │
  User ── HTTPS ──► API Gateway / ALB ──► FastAPI (App Runner/Fargate)    │
                                            │                             │
                                            ▼                             │
                                     Aurora PostgreSQL                     │
                                                                          │
  ── AUTONOMOUS UPDATES ───────────────────────────────────────────────── │
  EventBridge Scheduler ──► Lambda: SP-API sync (orders/inventory)  ──► DB│
  EventBridge Scheduler ──► Lambda: reports (fees/settlements)      ──► DB│
  EventBridge Scheduler ──► Lambda: PA-API price snapshot           ──► DB│
  SP-API Notifications ──► SQS (queue) ──► Lambda consumer          ──► DB│
  CloudWatch alarms ──► Lambda: self-heal (restart, replay DLQ)   ──► ops │
  DLQs + retry + exponential backoff on every pipeline                  │
                                                                          │
  ── SELF-UPDATING CODE ──────────────────────────────────────────────── │
  GitHub ──► Actions/CI ──► ECR image ──► rolling update (ECS/App Runner) │
                        └─────────────────────────────────────────────────┘
```

### 3.1 Components
- **Frontend** : React + Vite SPA served from S3 + CloudFront (cheap, cacheable). Bilingual FR/EN + USD/CAD display.
- **API** : FastAPI monolith in one container (auth, dashboards, research, admin) — fast to ship; split into services only if needed.
- **Store** : Aurora PostgreSQL (serverless v2) — relational fit, good for analytics SQL, JSONB for flexible payloads, timeseries-friendly for price snapshots.
- **Workers** : Lambda functions (one per pipeline) invoked by EventBridge Scheduler with cron expressions (support distinct US/CA cadences).
- **Webhooks** : SP-API Notifications destinations = SQS standard queue → Lambda consumer → upsert.
- **Files** : S3 bucket for report payloads (settlements, merchant listings) with lifecycle policy.
- **Secrets** : AWS Secrets Manager (LWA creds, SP-API client key/secret, PA-API keys), KMS-encrypted at rest; IAM least privilege.

### 3.2 Data model (initial)
```
marketplaces(id, code, marketplace_id, currency, iso)
sellers(id, name, story, credentials_ref)              -- credentials_ref = Secrets Manager arn
spapi_creds(seller_id, auth_model, client_id_arn, iam_role_arn, merchant_token_arn, status)
products(id, asin, title, brand, image_url, category, ...)
price_snapshots(id, product_id, marketplace_id, ours, price, buybox, currency, captured_at)
orders(id, seller_id, marketplace_id, amazon_order_id, purchase_date, status, ...)
order_items(id, order_id, product_id, qty, unit_price, ...)
fees(id, order_item_id, fee_type, amount, currency)
settlements(id, seller_id, marketplace_id, posted_date, gross, fees, net, currency)
settlement_lines(id, settlement_id, product_id, asin, sku, order_id, units, revenue, fees, net, currency)
inventory(id, product_id, quantity, conditions, snapped_at)
reports(id, report_kind, marketplace_id, status, s3_key, ran_at)
pipeline_runs(id, job, marketplace_id, status, started, finished, rows, error)
alerts(id, kind, severity, ref_type, ref_id, message, created_at)
notif_subscriptions(id, seller_id, notification_type, destination_arn, status)
app_users(id, username, password_hash, role, prefs)
```
- All money stored with ISO currency; conversions at view time via FX service (USD base — reuse pattern already proven in `Suivi_Importation`, live rates w/ cache).

### 3.3 Autonomous updates — specifics
1. **Scheduled pipelines** (`EventBridge Scheduler`, cron)
   - `orders-sync` every 30 min (US) / 60 min (CA) — incremental by `LastUpdateDate`.
   - `inventory-sync` daily.
   - `reports-fees` daily, `reports-settlements` weekly.
   - `price-snapshot` hourly for tracked ASINs (PA-API/SP-API pricing).
   - FX refresh cache 30 min.
2. **Webhooks (push)** — SP-API Notifications v2:
   - Register SQS destination per seller; subscribe `ORDER_CHANGE`, `FEE_PROMOTION`, (optionally) `LISTINGS_ITEM_STATUS_CHANGE`.
   - Lambda consumer validates `SellingPartnerApi` signature header, upserts, raises alerts.
3. **Self-repair / health**:
   - Health endpoint `/healthz` (DB, SP-API token, queue depth, last-sync age) on 1-min CloudWatch alarm.
   - Failed jobs → DLQ → replay worker (bounded attempts) → dead-letter S3 + alert on permanent failure.
   - Detect **stale data** (`last_ok_sync_at` per job) and re-trigger.
   - Auto-restart of app on repeated 5xx (ECS/App Runner health check).
4. **Self-updating code**:
   - Git push `main` → CI builds, tests, `docker buildx` → push ECR → rolling deploy.
   - DB migrations via Alembic image run as ECS task before app deploy.
   - Rollback = redeploy previous tag (kept N images).

---

## 4. Security
- Secrets only in Secrets Manager; IAM roles instead of access keys in code.
- App auth: Cognito user pool (admin/analyst/viewer) or lightweight session auth like the existing tracker; decision deferred to Phase 0.
- SP-API credentials per seller isolated; audit log of API calls (`pipeline_runs`).
- KMS encryption for DB, S3, backups. Private VPC for DB + workers; public only at ALB/CloudFront.

## 5. Bilingual & currency
- FR/EN toggle (existing pattern from Suivi_Importation reused). Dates + money formatted per locale. FX widget converting CAD ↔ USD transparently (base USD, live, cached).

## 6. Costs (indicative, small volume)
- Aurora serverless v2 ~ small monthly; Lambda pay-per-invocation; S3 pennies; CloudFront low; SQS negligible. Dominant variable = SP-API/PA-API call volume → throttling & incremental sync keep it bounded.

## 7. Roadmap
| Phase | Deliverable |
|---|---|
| 0 | Repo scaffold, CDK infra (VPC, RDS, S3, cognito/roles), CI skeleton, health check — done |
| 1 | SP-API auth adapter (key model US+CA), orders sync + dashboard — done (17 tests green) |
| 2 | Reports (inventory, fees, settlements), margin analytics — done (29 tests green) |
| 3 | Market research: PA-API search + price snapshots + FX, cross-market compare — done (48 tests green) |
| 3w | Web dashboard: React SPA, dark mode, bilingual EN/FR, all analytics endpoints — done (build green, 8 routes) |
| 4 | Notifications/webhooks → alerts (price drop, stock-out, margin erosion) — done (59 tests green; rules engine, alerts-eval worker, idempotent webhook consumer) |
| 5 | Autonomous hardening (DLQ replay, stale detection, auto-deploy rollback), audit + docs — done (68 tests green; `self-heal` worker every 5 min, DLQ replay bounded 3 attempts w/ exponential backoff, stale re-trigger w/ claim guard, stale-run cleanup, SNS alarms for Lambda errors/DLQ depth/ECS/ALB/Aurora, CI ECS rollback) |
| 6 | Multidimensional analysis (category → niche → family → ASIN) + explainable demand forecasting — done (86 tests green; taxonomy + forecast workers on daily cron, category tree rollups + family→ASIN drill + seasonal-OLS 30-day forecast endpoints, Categories & Forecast routes in the SPA) |
| 7 | AI assistant/chatbot grounded in collected data + compliance alerts — done (102 tests green; deterministic bilingual NLU, chat send/history/prefs/predictions endpoints, per-session conversations, predictive gainers/decliners/next-30d insights, Assistant route in the SPA with inline tables + charts) |

## 8. Decisions (confirmed in Phase 0)
1. End-user auth: **Cognito user pool** (admin/analyst/viewer groups; `dev` mode for local).
2. Framework: **FastAPI**.
3. Web hosting: **ECS Fargate + ALB**.
4. Storage: **Aurora PostgreSQL (serverless v2)**.