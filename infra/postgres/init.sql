-- Call-Agent DB スキーマ (M2.5: 通話履歴 + 文字起こし永続化)
-- DESIGN.md §5.5 のデータモデルのサブセット。tenant_id は将来のマルチテナント化 (§7) に備えて最初から持つ。
-- lines / number_policies / chips / agent_configs は該当マイルストーン (M4〜) で追加する。

CREATE TABLE IF NOT EXISTS calls (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     text NOT NULL DEFAULT 'default',
  direction     text NOT NULL DEFAULT 'inbound',   -- inbound / outbound (M6)
  caller_number text,                              -- NULL = 非通知/取得失敗
  callee_number text,
  room_name     text NOT NULL UNIQUE,              -- LiveKitルーム名 (dispatch ruleが通話ごとに一意生成)
  answered_by   text NOT NULL DEFAULT 'ai',        -- ai / human / ai_then_human (M3) / missed / blocked (M4)
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz,                       -- NULL = 通話中
  summary_md    text,                              -- AIサマリー (M3)
  recording_path text,                             -- 録音 (M3)
  source        text NOT NULL DEFAULT 'live'       -- live / imported (M9)
);

CREATE INDEX IF NOT EXISTS calls_started_at_idx ON calls (started_at DESC);
CREATE INDEX IF NOT EXISTS calls_caller_idx ON calls (caller_number, started_at DESC);

CREATE TABLE IF NOT EXISTS transcript_segments (
  id          bigserial PRIMARY KEY,
  call_id     uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  seq         int NOT NULL,
  speaker     text NOT NULL,                       -- caller / ai / user(=本人が交代後に話した分, M3)
  text        text NOT NULL,
  interrupted boolean NOT NULL DEFAULT false,
  at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (call_id, seq)
);

CREATE INDEX IF NOT EXISTS segments_call_idx ON transcript_segments (call_id, seq);

-- Directory Agent (別プロセスworker) へのジョブキュー (M2.75: ask_workspace)
CREATE TABLE IF NOT EXISTS agent_jobs (
  id          bigserial PRIMARY KEY,
  call_id     uuid REFERENCES calls(id) ON DELETE SET NULL,
  kind        text NOT NULL DEFAULT 'workspace_query',
  query       text NOT NULL,
  status      text NOT NULL DEFAULT 'pending',   -- pending / running / done / error
  result      text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS agent_jobs_status_idx ON agent_jobs (status, id);

-- フラグメント (思考の断片): 会話監視エージェントが管制室に浮かべる吹き出し。
-- 本人だけが見る画面なので【本人限定】情報も可 (AI向けの agent_note とは開示基準が異なる)
CREATE TABLE IF NOT EXISTS fragments (
  id          bigserial PRIMARY KEY,
  call_id     uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  kind        text NOT NULL DEFAULT 'info',      -- info / alert / hint (自由拡張可)
  title       text NOT NULL DEFAULT '',          -- 常時表示の短い見出し
  text        text NOT NULL,                     -- クリック展開で表示される内容
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fragments_call_idx ON fragments (call_id, id);

-- ===== 警備 (SIP不正利用の検知・遮断、2026-07-26) =====
-- 実装と判定ロジックは services/agent/security.py。init.sql は初回 initdb でしか走らないため、
-- **同じDDLを security.ensure_schema() が起動時に冪等に流す** (稼働中DBへの追加用)。
-- 片方だけ直すと両者がずれるので、変更時は必ず両方を直すこと。

-- 着信の試行ログ。拒否した呼は calls 行を作らないので、レート制限とストーム判定の母数はここ
CREATE TABLE IF NOT EXISTS call_attempts (
  id            bigserial PRIMARY KEY,
  at            timestamptz NOT NULL DEFAULT now(),
  caller_number text,
  callee_number text,
  source        text NOT NULL DEFAULT 'hookd',   -- hookd (Asteriskの関門) / agent (最終防衛線)
  verdict       text NOT NULL DEFAULT 'allow',   -- allow / reject / observe
  kind          text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS call_attempts_at_idx ON call_attempts (at DESC);
CREATE INDEX IF NOT EXISTS call_attempts_caller_idx ON call_attempts (caller_number, at DESC);

-- 検知イベント (管制室のアラート + Webhook通知の元)。同種・同一発信者の連続は1行にまとめる
CREATE TABLE IF NOT EXISTS security_events (
  id            bigserial PRIMARY KEY,
  first_at      timestamptz NOT NULL DEFAULT now(),
  last_at       timestamptz NOT NULL DEFAULT now(),
  count         int NOT NULL DEFAULT 1,
  kind          text NOT NULL,                   -- extension_scan / premium_relay / rate_limit ...
  severity      text NOT NULL DEFAULT 'warn',    -- info / warn / critical
  source        text NOT NULL DEFAULT 'hookd',
  action        text NOT NULL DEFAULT 'reject',  -- reject / observe / alert
  caller_number text,
  callee_number text,
  detail        text NOT NULL DEFAULT '',
  acknowledged  boolean NOT NULL DEFAULT false,
  notified_at   timestamptz
);
CREATE INDEX IF NOT EXISTS security_events_last_idx ON security_events (last_at DESC);

-- ブロックリスト。自動ブロックは時限 (既定1時間)、管制室から手動で入れた分は恒久 (NULL)
CREATE TABLE IF NOT EXISTS blocked_callers (
  number        text PRIMARY KEY,
  reason        text NOT NULL DEFAULT '',
  blocked_until timestamptz,
  hits          int NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 管制室の設定 (key-value)。assistant_enabled = 留守電ON/OFF (offならAsteriskがAIにつながない)
CREATE TABLE IF NOT EXISTS settings (
  key         text PRIMARY KEY,
  value       text NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO settings (key, value) VALUES ('assistant_enabled', 'true') ON CONFLICT (key) DO NOTHING;
-- 声のメイン/サブ (2026-07-30)。空文字 = サーバーの .env の既定に任せる。
-- agent が通話ごとに読むので、管制室の設定画面での変更は次の通話から効く (再起動不要)。
-- ⚠既存DBにはこの行が無いままでも動く (agent側が「行が無ければ環境変数」にフォールバックする)
INSERT INTO settings (key, value) VALUES ('tts_primary', '') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('tts_fallback', '') ON CONFLICT (key) DO NOTHING;

-- スマホアプリのプッシュトークン (FCM、2026-09-18)。着信と取り次ぎの瞬間に hookd が「起きろ」を送る。
-- ⚠既存DBには hookd の起動時 (push.DDL) で入る
CREATE TABLE IF NOT EXISTS device_push_tokens (
  token       text PRIMARY KEY,
  name        text NOT NULL DEFAULT '',
  platform    text NOT NULL DEFAULT 'android',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
-- 端末ごとの一時停止 (2026-09-24)。true の端末には「起きろ」を送らない
ALTER TABLE device_push_tokens ADD COLUMN IF NOT EXISTS paused boolean NOT NULL DEFAULT false;
