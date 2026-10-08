#!/usr/bin/env python3
"""テスト通話で、終話後の電話帳の更新 (人ごとの md など) を試す (2026-09-26)。

なぜ要るか: 本番の worker はテスト通話 (ルーム名が sim) では電話帳を書かない — 自動テストは
毎回違うダミー番号を使うので、書かせると空の md が溜まる。それでは電話帳の更新を実回線でしか
試せないので、終わったテスト通話を指定して、要約から電話帳の更新までをこの場で走らせる。
⚠開発の道具なので製品の外 (ここ) に置く。⚠実際のワークスペースに書く — 試したら --clean で消す

使い方 (VM 上。worker のコンテナの中で動かす — 依存と環境変数がそろっている):
  sudo docker cp infra/scripts/try_phonebook_update.py infra-directory-agent-1:/app/
  sudo docker exec infra-directory-agent-1 python try_phonebook_update.py <通話のid> [...]
  sudo docker exec infra-directory-agent-1 python try_phonebook_update.py --clean <番号>
"""

import asyncio
import os
import shutil
import sys

import asyncpg

import worker as w


async def main(args: list[str]) -> int:
    if args[:1] == ["--clean"]:
        d = w.BOOK.dir(args[1])
        shutil.rmtree(d, ignore_errors=True)
        print("消した:", d)
        return 0
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    await w.refresh_lookup_names(pool)
    for cid in args:
        c = await pool.fetchrow(
            "SELECT id, caller_number, room_name, started_at, ended_at FROM calls WHERE id = $1", cid
        )
        if not c or not c["ended_at"]:
            print("終わった通話ではない:", cid)
            continue
        summary, org, person, conflict = await w.summarize_call(pool, c)
        print(f"{cid}: 要約={summary!r} 組織={org!r} 個人={person!r} 食い違い={conflict!r}")
        # ⚠テスト通話の印 (sim) を外して本番と同じ処理を通す
        c = dict(c, room_name=f"call_{c['caller_number']}_try")
        w.update_phonebook_after_call(c, summary, org, person, conflict)
    await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
