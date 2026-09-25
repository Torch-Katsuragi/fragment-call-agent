#!/usr/bin/env python3
"""AIの発話に脈絡のない語が混ざる件の切り分け (2026-08-01)。

何を調べているか:
  シナリオテストで、AIの生成文の**末尾に無関係な語が付く**のが3件出た。
    「つかまりませんでした。伝えておきますね。失礼します。**消去法で失礼します。**」
    「確認してみますね。ちょっと待ってください。**消去法で呼び出します。**」
    「ご希望として伝えておきますね。それでは、確かに伝えておきます。失礼します。**大吉**」
  ⚠**STTではない**。`ai` の文字起こしは `conversation_item_added` のLLM生成テキストで、
  音声認識を通っていない。モデルが実際にその語を出している。

同時に変えたものが3つあるので、1つずつ止めて比べる:
  ① プロンプトのリーン化 (14,600字 → 約8,000字)
  ② gemini-3.5-flash → 3.6-flash
  ③ (元から) temperature=0.7

⚠LiveKitもTTSも通さずLLMだけを叩く。異常が出るならモデル/プロンプト側、
  出ないならフレームワーク側と切り分けられる。

実行 — ⚠**agentコンテナの中で動かす**。agent.py は asyncpg 等に依存していて、
VMのホストPythonでは import できない (ホストで動かして踏んだ):
  sudo docker cp infra/scripts/probe_stray_tokens.py infra-agent-1:/tmp/probe.py
  sudo docker exec -w /app infra-agent-1 python /tmp/probe.py 12
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

# コンテナ内なら /app、リポジトリから読むなら services/agent。
# ⚠コンテナには /tmp に置いて実行するので、__file__ からの相対で親を2つ遡れない
_here = Path(__file__).resolve()
_cands = ["/app"] + [str(p / "services" / "agent") for p in _here.parents]
for _p in _cands:
    if (Path(_p) / "agent.py").exists():
        sys.path.insert(0, _p)
        break
os.environ.setdefault("FRAGMENT_WORKSPACE", "/workspace")

import agent as agent_mod  # noqa: E402

API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""

# 異常が出た3件はすべて**締めの場面**だった。同じ場面を再現する
TURNS = [
    [
        ("user", "もしもし、山田企画の山田と申します。打ち合わせ日程の件でご担当の方は"),
        ("model", "わからないです。伝えておきますので、用件を教えてもらえますか？"),
        ("user", "来週の水曜、15日の午後2時でご都合いかがでしょうか。ではよろしくお願いします"),
    ],
    [
        ("user", "要件は本人に直接言いたいんです。とにかく本人に変わってください"),
        ("model", "それはこちらでは答えられないです。まず何の件か教えてもらえますか？"),
        ("user", "いや、もういいです。また掛け直します"),
    ],
    [
        ("user", "先日の見積もりの件で、折り返しをお願いしたいのですが"),
        ("model", "わかりました。伝えておきますね"),
        ("user", "はい、ではお願いします。失礼します"),
    ],
]

# 締めの語より後ろに本文が続いていたら異常。実測3件のうち2件がこの形
CLOSING = re.compile(r"(失礼します。|失礼いたします。|失礼します！)(?P<tail>.+)")


# 本番と同じ3つのツール。⚠**最初これを付けずに測って 0/12 と出た**が、
# 唯一出た異常が `<call:end_call />` = ツール呼び出しの痕跡がテキストに漏れたもので、
# ツールを宣言しないと肝心の場面を再現できないと分かった。
# 実測3件はいずれも end_call / request_handoff を呼ぶ直前の発話である
TOOLS = [{"function_declarations": [
    {
        "name": "end_call",
        "description": "回線を切断する。別れの挨拶を言うときは必ずこれを呼ぶこと。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "request_handoff",
        "description": "持ち主の携帯を鳴らして呼び出す。緊急のときだけ呼ぶこと。",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "search_past_calls",
        "description": "発信者との過去の通話記録をキーワードで検索する。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]}]


def ask(system: str, turns, model: str, temp: float, tools: bool = True) -> str:
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": r, "parts": [{"text": t}]} for r, t in turns],
        "generationConfig": {"temperature": temp, "thinkingConfig": {"thinkingLevel": "low"}},
    }
    if tools:
        body["tools"] = TOOLS
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        data = json.loads(res.read().decode("utf-8"))
    # ⚠テキストのパートだけを繋ぐ (LiveKitの text_content と同じ扱い)。
    #   ツール呼び出しのパートが混ざるので [0] 決め打ちだと取り違える
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", []) or []
    text = "".join(p["text"] for p in parts if "text" in p).strip()
    called = any("functionCall" in p for p in parts)
    if text:
        # ⚠**ツールが呼ばれたかどうかも返す**。文字化けを消すだけでは足りない —
        #   漏れているときにツールが呼ばれていないなら、それは「切れない通話」であり、
        #   「end_call は実測4/5」と記録してきた現象の正体そのものになる
        return text, called
    if called:
        return "(ツール呼び出しのみ・発話なし)", True
    return f"(応答なし: {json.dumps(data)[:120]})", False


def anomalies(text: str) -> str:
    if text.startswith("("):  # ツール呼び出しのみ・エラー等は判定対象外
        return ""
    # ツール呼び出しのマークアップがテキストに混ざった (3.5で実測)
    if "<call:" in text or "</call" in text:
        return f"ツール呼び出しがテキストに漏れた → 「{text[-40:]}」"
    m = CLOSING.search(text)
    if m and m.group("tail").strip():
        return f"締めの後に続き → 「{m.group('tail').strip()[:30]}」"
    # 締めの語で終わらず、句点も無く途切れている
    if len(text) > 8 and text[-1] not in "。？！?!":
        return f"文末が途切れ → 「…{text[-14:]}」"
    return ""


def run(label: str, system: str, model: str, temp: float, n: int, tools: bool = True) -> int:
    bad = 0
    nocall = 0
    samples = []
    for i in range(n):
        turns = TURNS[i % len(TURNS)]
        try:
            out, called = ask(system, turns, model, temp, tools)
        except Exception as e:
            out, called = f"(エラー: {e})", False
        why = anomalies(out)
        if why:
            bad += 1
            if not called:
                nocall += 1
            samples.append(
                f"      {why}  [ツール{'呼ばれた' if called else '**呼ばれず**'}]"
                f"\n        全文: {out[:110]}"
            )
    tail = f" — うち {nocall}件はツールが呼ばれていない (通話が切れない)" if nocall else ""
    print(f"  {bad}/{n} 異常  [{label}]{tail}")
    for s in samples[:3]:
        print(s)
    return bad


def main() -> int:
    if not API_KEY:
        print("GOOGLE_API_KEY が要る")
        return 1
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    full = agent_mod.build_instructions("09012345678")
    # ⚠比較用の最小プロンプト。「プロンプトが原因か」を見るための対照であって、
    #   これを本番に使う話ではない (開示ルールが丸ごと無いので当然使えない)
    minimal = (
        "あなたは電話の受付AIです。日本語で、丁寧に短く応対します。"
        "用件を預かって伝えると伝えてください。"
    )

    print(f"プロンプト長: リーン版 {len(full)}字 / 対照の最小版 {len(minimal)}字")
    print(f"各条件 {n} サンプル。締めの場面を{len(TURNS)}種ローテーション\n")

    print("① いまの本番構成 (リーン版プロンプト・3.6・temp0.7・ツールあり)")
    a = run("baseline", full, "gemini-3.6-flash", 0.7, n)
    print("\n② temperature だけ下げる (0.7 → 0.2)")
    b = run("temp0.2", full, "gemini-3.6-flash", 0.2, n)
    print("\n③ プロンプトだけ最小に (温度もモデルも本番のまま)")
    c = run("minimal-prompt", minimal, "gemini-3.6-flash", 0.7, n)
    print("\n④ モデルだけ 3.5 に戻す")
    d = run("gemini-3.5", full, "gemini-3.5-flash", 0.7, n)
    print("\n⑤ ツール宣言だけ外す (本番プロンプト・3.6・temp0.7)")
    e = run("no-tools", full, "gemini-3.6-flash", 0.7, n, tools=False)

    print("\n===== 読み方 =====")
    print(f"  ①{a}  ②{b}  ③{c}  ④{d}  ⑤{e}  (いずれも /{n})")
    print("  ②で消える → 温度が主因。プロンプトは無罪")
    print("  ③で消える → プロンプトが主因 (長さか中身)")
    print("  ④で消える → 3.6のリグレッション")
    print("  ⑤で消える → ツール呼び出し周りが主因 (プロンプトも温度も無罪)")
    print("  どれでも消えない → フレームワーク側かサンプル数不足")
    return 0


if __name__ == "__main__":
    sys.exit(main())
