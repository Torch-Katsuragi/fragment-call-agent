#!/usr/bin/env python3
"""Live API のモデルが「関数呼び出しを出すか」だけを、生のSDKで確かめる切り分け実験。

なぜ要るか (2026-09-18):
  `AI_CORE=live` + gemini-3.8-live で run_scenarios を回したら、AIは「確認してみますね」と
  言うのに **hookd に取り次ぎ要求が1件も届かない**。agent のログにも
  `request_handoff invoked by AI` が出ない。
  プラグインには tool_choice='none' のとき呼び出しを捨てる経路があるが、そのときに出る
  `rejecting tool call requested while tool_choice='none'` の警告は**出ていなかった**。
  つまり「捨てられた」のではなく「モデルが出していない」。

  ただしそれだけでは **①モデルが呼ばない ②プラグインが宣言を送れていない** の
  区別がつかない。ここを **agent とプラグインを丸ごと外して** 生のSDKで確かめる。

実行 (VM上、リポジトリ直下から):
  python3 infra/scripts/probe_live_tools.py                       # 既定の3モデルを比較
  python3 infra/scripts/probe_live_tools.py gemini-3.8-live       # 指定のみ

⚠テキスト入力で試す。音声にしないのは、音声認識の失敗と混ざると切り分けにならないため。
⚠GOOGLE_API_KEY はリポジトリ直下の .env から読む。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MODELS = [
    "gemini-3.1-flash-live-preview",  # 従来
    "gemini-3.8-live",  # 今回の候補
]

# 呼ばせたい道具。**呼ばないと答えようがない**用件にする
TOOL_NAME = "request_handoff"
INSTRUCTIONS = (
    "あなたは電話の一次応対。相手が緊急だと言ったら、必ず request_handoff を呼んで"
    "持ち主を呼び出すこと。呼び出しは道具でしか行えない。"
)
USER_TURN = "水道管が破裂して水が吹き出しています。大至急ご本人に代わってください。"


def load_api_key() -> str:
    env = REPO_ROOT / ".env"
    if not env.exists():
        sys.exit(f".env が見つかりません: {env}")
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*GOOGLE_API_KEY=(.*)$", line)
        if m:
            return m.group(1).strip()
    sys.exit(".env に GOOGLE_API_KEY がありません")


async def probe(model: str, api_key: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})

    tools = [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=TOOL_NAME,
                    description="持ち主の端末を鳴らして呼び出す。緊急のときだけ使う",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "reason": types.Schema(
                                type=types.Type.STRING, description="呼び出す理由"
                            )
                        },
                        required=["reason"],
                    ),
                )
            ]
        )
    ]
    # ⚠**TEXT 出力は両モデルとも非対応** (2026-09-18に実測: APIError 1007
    #   "The requested combination of response modalities (TEXT) is not supported")。
    #   Live のこれらは音声専用なので AUDIO で繋ぎ、発話内容は出力側の文字起こしで拾う。
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(parts=[types.Part(text=INSTRUCTIONS)]),
        tools=tools,
    )

    calls: list[str] = []
    text: list[str] = []
    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=USER_TURN)]),
                turn_complete=True,
            )
            async for resp in session.receive():
                if resp.tool_call and resp.tool_call.function_calls:
                    for fc in resp.tool_call.function_calls:
                        calls.append(f"{fc.name}({fc.args})")
                    break  # 呼ばれたことが分かれば十分
                sc = resp.server_content
                if sc and sc.output_transcription and sc.output_transcription.text:
                    text.append(sc.output_transcription.text)
                if resp.text:
                    text.append(resp.text)
                if sc and sc.turn_complete:
                    break
    except Exception as e:  # noqa: BLE001
        return f"ERROR {type(e).__name__}: {e}"

    said = "".join(text).strip().replace("\n", " ")[:70]
    if calls:
        return f"呼んだ: {calls[0][:90]}"
    return f"呼ばない (発話のみ): 「{said}」"


async def main() -> None:
    models = sys.argv[1:] or DEFAULT_MODELS
    api_key = load_api_key()
    print(f"道具 {TOOL_NAME} を宣言し、緊急の用件をテキストで1ターン投げる\n")
    for m in models:
        result = await probe(m, api_key)
        print(f"  {m:34s} {result}")
    print(
        "\n判定: 3.8 が「呼ばない」なら**モデル側**。"
        "「呼んだ」ならプラグイン/agent 側の配線を疑う"
    )


if __name__ == "__main__":
    asyncio.run(main())
