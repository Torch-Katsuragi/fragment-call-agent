# -*- coding: utf-8 -*-
"""メンバー・招待・端末のログイン (2026-09-26)。

誰が管制室とアプリを使えるかを Google アカウントで決める (それまでは管制室が発行する合言葉 =
端末トークンで、持っていれば誰でも全部の権限で入れた)。
  members          … メールアドレスごとのメンバーと権限 (owner / admin / responder / viewer)。
                     status = banned で締め出す (その人の端末もまとめて切れる)
  invites          … 招待。トークンはハッシュだけ持つ。招待したメールアドレスの Google アカウントでしか受けられない
  device_sessions  … アプリの端末ごとのログイン。トークンはハッシュだけ持つ
判定と画面は管制室 (services/dashboard/lib/members.ts・middleware.ts)。ここは表を作るだけ
(hookd の起動時に流す。init.sql にも同じもの)。
"""

DDL = """
CREATE TABLE IF NOT EXISTS members (
  id          bigserial PRIMARY KEY,
  email       text NOT NULL UNIQUE,
  name        text NOT NULL DEFAULT '',
  role        text NOT NULL DEFAULT 'viewer',
  status      text NOT NULL DEFAULT 'active',
  invited_by  bigint,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS invites (
  token_hash  text PRIMARY KEY,
  email       text NOT NULL,
  role        text NOT NULL,
  created_by  bigint,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  accepted_at timestamptz,
  revoked_at  timestamptz,
  mailed_at   timestamptz
);
CREATE TABLE IF NOT EXISTS device_sessions (
  token_hash  text PRIMARY KEY,
  member_id   bigint NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  device_id   text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_seen   timestamptz NOT NULL DEFAULT now(),
  revoked_at  timestamptz
);
-- ⚠端末は名前も役割も持たない (2026-10-04 ユーザー「端末ごとに見分けたいなら端末ごとに別のアカウントを入れればいい」)。
--   担当は人 (members.duty) に移す。端末ごとの権限の上限 (同日に足した role_cap) もやめた
ALTER TABLE members ADD COLUMN IF NOT EXISTS duty text NOT NULL DEFAULT '';
ALTER TABLE device_sessions DROP COLUMN IF EXISTS role_cap;
-- ⚠端末の情報はログイン (device_sessions) の 1 行だけに持つ (2026-10-04 ユーザー「端末固有の情報はできるだけ持たない。
--   システムはシンプルに」)。起こす宛先 (FCM) と一時停止もここ。ログアウトすれば宛先ごと消える。
--   それまでの devices (見かけた時刻と持ち主 = ここと同じ) と device_push_tokens (宛先) は移して消す
ALTER TABLE device_sessions ADD COLUMN IF NOT EXISTS push_token text;
ALTER TABLE device_sessions ADD COLUMN IF NOT EXISTS paused boolean NOT NULL DEFAULT false;
DO $$ BEGIN
  IF to_regclass('device_push_tokens') IS NOT NULL THEN
    UPDATE device_sessions s SET push_token = t.token, paused = t.paused
      FROM device_push_tokens t
      WHERE t.device_id = s.device_id AND s.revoked_at IS NULL AND s.push_token IS NULL;
    DROP TABLE device_push_tokens;
  END IF;
  IF to_regclass('devices') IS NOT NULL THEN
    DROP TABLE devices;
  END IF;
  -- ⚠一時停止は端末ごと (2026-10-04)。同日に人ごと (members.paused) にして戻したので、その値を端末へ返す
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'members' AND column_name = 'paused') THEN
    UPDATE device_sessions s SET paused = true FROM members m
      WHERE m.paused AND s.member_id = m.id AND s.revoked_at IS NULL;
    ALTER TABLE members DROP COLUMN paused;
  END IF;
END $$;
"""

# いま使える端末 = 有効なログイン (締め出されていない人の、切られていないログイン)。FROM 句にそのまま足す
ACTIVE_SESSIONS = """device_sessions s JOIN members m ON m.id = s.member_id
    AND s.revoked_at IS NULL AND m.status = 'active'"""
