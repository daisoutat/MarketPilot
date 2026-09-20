"""MarketPilot shared package (used by the API service and the Lambda workers).

Modules:
  config      MP_* settings + Secrets Manager DSN resolution
  db          async SQLAlchemy engine/session
  models      initial data model (Aurora PostgreSQL)
  secretstore AWS Secrets Manager reader (thin, sync)
  spapi       Amazon SP-API: auth providers (key + LWA), SigV4, client
  orders      orders sync pipeline (incremental, idempotent)
  telemetry   pipeline_runs bookkeeping
"""