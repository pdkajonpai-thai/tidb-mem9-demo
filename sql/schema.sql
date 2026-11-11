-- TiDB schema (ARCHITECTURE.md section 3).
-- scripts/seed.py strips comment lines, then splits on semicolons and runs each statement.
-- LangGraph checkpoint tables (lg_*) are created by TiDBSaver.setup().
-- Indexes only, no foreign keys, to keep the demo reset simple.

-- User profiles
CREATE TABLE IF NOT EXISTS users (
  id             INT PRIMARY KEY,
  name           VARCHAR(64)  NOT NULL,
  name_en        VARCHAR(64)  NOT NULL,
  risk_profile   VARCHAR(16)  NOT NULL,          -- conservative / balanced / aggressive
  max_risk_level TINYINT      NOT NULL,          -- 1 (low) to 5 (high)
  base_currency  VARCHAR(8)   NOT NULL DEFAULT 'KRW'
);

-- Exchanges and protocol access paths
CREATE TABLE IF NOT EXISTS exchanges (
  id     INT PRIMARY KEY,
  name   VARCHAR(64) NOT NULL,
  type   VARCHAR(8)  NOT NULL,                   -- CEX / DEX
  region VARCHAR(8)  NOT NULL                    -- KR (domestic exchange) / GLOBAL (overseas exchange) / ONCHAIN
);

-- Location (chain) column: an on-chain network (e.g. arbitrum) or an exchange account (e.g. binance)

CREATE TABLE IF NOT EXISTS exchange_chains (
  exchange_id INT         NOT NULL,
  chain       VARCHAR(32) NOT NULL,
  PRIMARY KEY (exchange_id, chain)
);

CREATE TABLE IF NOT EXISTS user_exchange_accounts (
  user_id     INT         NOT NULL,
  exchange_id INT         NOT NULL,
  status      VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
  PRIMARY KEY (user_id, exchange_id)
);

-- Token master
CREATE TABLE IF NOT EXISTS assets (
  symbol   VARCHAR(16) NOT NULL,
  chain    VARCHAR(32) NOT NULL,
  category VARCHAR(16) NOT NULL,                 -- stable / major / alt
  PRIMARY KEY (symbol, chain)
);

-- Real-time holdings
CREATE TABLE IF NOT EXISTS user_holdings (
  user_id      INT            NOT NULL,
  asset_symbol VARCHAR(16)    NOT NULL,
  chain        VARCHAR(32)    NOT NULL,
  amount       DECIMAL(24, 8) NOT NULL,
  usd_value    DECIMAL(16, 2) NOT NULL,
  updated_at   TIMESTAMP      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, asset_symbol, chain)
);

-- Month-end balance history on overseas exchanges (shown as reference against the KRW 500M overseas account reporting threshold)
CREATE TABLE IF NOT EXISTS balance_snapshots (
  user_id      INT            NOT NULL,
  month_end    DATE           NOT NULL,
  exchange_id  INT            NOT NULL,
  asset_symbol VARCHAR(16)    NOT NULL,
  amount       DECIMAL(24, 8) NOT NULL,
  price_usd    DECIMAL(16, 4) NOT NULL,
  krw_value    DECIMAL(18, 0) NOT NULL,
  PRIMARY KEY (user_id, month_end, exchange_id, asset_symbol)
);

-- DeFi protocols and exchange deposit/staking products (one row per name + location)
CREATE TABLE IF NOT EXISTS protocols (
  id         INT PRIMARY KEY,
  name       VARCHAR(64)    NOT NULL,
  chain      VARCHAR(32)    NOT NULL,
  tvl_usd    DECIMAL(20, 2),
  audited    BOOLEAN        NOT NULL,
  risk_score TINYINT        NOT NULL,            -- 1 (low) to 5 (high)
  UNIQUE KEY uk_protocol (name, chain)
);

-- Strategy templates the execution engine understands
CREATE TABLE IF NOT EXISTS strategies (
  id              INT PRIMARY KEY,
  type            VARCHAR(16) NOT NULL,          -- swap / lend / stake / lp
  description     VARCHAR(255) NOT NULL,
  action_template JSON NOT NULL
);

-- Collected raw signals (synthetic seed)
CREATE TABLE IF NOT EXISTS raw_signals (
  id        INT PRIMARY KEY,
  source    VARCHAR(32)   NOT NULL,              -- x / telegram / blog / youtube / announcement
  author    VARCHAR(64)   NOT NULL,
  content   VARCHAR(2000) NOT NULL,
  posted_at DATETIME      NOT NULL
);

-- Per-visitor workspaces (hash of the Google account email; the email itself is not stored)
-- Analysis results, recommendations, checkpoints, and mem9 memories are kept per workspace, so each resets independently
CREATE TABLE IF NOT EXISTS workspaces (
  id         VARCHAR(16) PRIMARY KEY,
  created_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
  reset_at   TIMESTAMP   NULL
);

-- Raw signals already analyzed, per workspace
CREATE TABLE IF NOT EXISTS analyzed_signals (
  workspace   VARCHAR(16) NOT NULL,
  signal_id   INT         NOT NULL,
  analyzed_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (workspace, signal_id)
);

-- Investment opportunities structured by the LLM (the DB auto-embeds summary)
CREATE TABLE IF NOT EXISTS opportunities (
  id           BIGINT PRIMARY KEY AUTO_RANDOM,
  workspace    VARCHAR(16)  NOT NULL,
  signal_id    INT          NOT NULL,
  title        VARCHAR(128) NOT NULL,
  asset        VARCHAR(16)  NOT NULL,
  chain        VARCHAR(32)  NOT NULL,
  protocol_id  INT          NOT NULL,
  strategy_id  INT          NOT NULL,
  action_type  VARCHAR(16)  NOT NULL,
  expected_apy DECIMAL(6, 2),
  risk_level   TINYINT      NOT NULL,
  horizon      VARCHAR(16)  NOT NULL,
  confidence   DECIMAL(3, 2) NOT NULL,
  summary      VARCHAR(1500) NOT NULL,
  rationale    TEXT,
  status       VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
  created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  summary_vec  VECTOR(1024) GENERATED ALWAYS AS (EMBED_TEXT(
                 "tidbcloud_free/cohere/embed-multilingual-v3",
                 summary,
                 '{"input_type": "search_document", "input_type@search": "search_query"}'
               )) STORED,
  KEY idx_opp_ws (workspace, status),
  KEY idx_opp_signal (signal_id),
  VECTOR INDEX idx_opp_vec ((VEC_COSINE_DISTANCE(summary_vec)))
);

-- Recommendation history (linked to LangGraph threads)
CREATE TABLE IF NOT EXISTS recommendations (
  id             BIGINT PRIMARY KEY AUTO_RANDOM,
  workspace      VARCHAR(16)  NOT NULL,
  user_id        INT          NOT NULL,
  opportunity_id BIGINT       NOT NULL,
  thread_id      VARCHAR(64)  NOT NULL,
  `rank`         TINYINT      NOT NULL,
  rationale      TEXT         NOT NULL,
  memory_refs    JSON,
  command        JSON,                           -- ExecutionCommand (preview on the approval screen)
  model          VARCHAR(64)  NOT NULL,
  status         VARCHAR(24)  NOT NULL DEFAULT 'AWAITING_APPROVAL',  -- / APPROVED / REJECTED
  decided_at     TIMESTAMP    NULL,
  created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  KEY idx_rec_ws_user_status (workspace, user_id, status),
  KEY idx_rec_thread (thread_id)
);

-- Execution orders and status (mock engine)
CREATE TABLE IF NOT EXISTS execution_orders (
  id                BIGINT PRIMARY KEY AUTO_RANDOM,
  recommendation_id BIGINT      NOT NULL,
  command           JSON        NOT NULL,
  status            VARCHAR(16) NOT NULL DEFAULT 'PENDING',  -- / SUBMITTED / CONFIRMED / FAILED
  tx_hash           VARCHAR(80),
  created_at        TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_order_rec (recommendation_id)
);

-- Access and usage logs (sign-ins, LLM usage). Not cleared by demo reset (not in APP_TABLES in scripts/seed.py)
CREATE TABLE IF NOT EXISTS access_logs (
  id            BIGINT PRIMARY KEY AUTO_RANDOM,
  at            TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
  event         VARCHAR(32)   NOT NULL,          -- visit / login / logout / llm:* / admin:*
  email         VARCHAR(255),
  name          VARCHAR(255),
  ip            VARCHAR(64),                     -- left empty: the current hosting doesn't pass the real client IP
  forwarded_for VARCHAR(255),                    -- left empty: used if moved to hosting that passes the IP
  user_agent    VARCHAR(512),
  session_id    VARCHAR(64),
  detail        JSON,
  KEY idx_access_at (at),
  KEY idx_access_email_at (email, at)
);

-- Translation cache for display (source hash + target language). Identical source text is not translated again.
-- Reused across app restarts and other sessions. Keyed on source text, so not cleared by demo reset (not in APP_TABLES)
CREATE TABLE IF NOT EXISTS translations (
  src_hash    CHAR(64)    NOT NULL,              -- SHA-256 of the source text
  lang        VARCHAR(8)  NOT NULL,              -- target language (ko / en)
  src         TEXT        NOT NULL,
  text        TEXT        NOT NULL,
  created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (src_hash, lang)
);
