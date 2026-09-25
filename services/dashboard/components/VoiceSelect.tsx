"use client";

import { useEffect, useState } from "react";

// 声 (TTS) のメイン/サブ切替 (2026-07-30)。
// agent が**通話ごとに**settingsテーブルを読むので、変更は次の通話から効く。
// ⚠サブを置く理由: 外部APIに声を預けると、落ちたときに電話が無言になる。
//   通話で一番痛い障害がそれなので、合成が失敗したらフレームワークが次に切り替える。
const LABELS: Record<string, string> = {
  aivis: "Aivis (コハク)",
  google: "Google Cloud TTS (Chirp3-HD)",
  gemini: "Gemini TTS",
  elevenlabs: "ElevenLabs",
  voicevox: "VOICEVOX (要ローカルENGINE)",
};

const NOTES: Record<string, string> = {
  aivis: "子供っぽい声。初回0.1秒・1万字¥440 (従量)",
  google: "自然で速いが機械的。1万字約¥45",
  gemini: "既存のGOOGLE_API_KEYで動くが初動1〜2秒",
  elevenlabs: "日本語の漢字読み間違いが多く不採用 (2026-07-18)",
  voicevox: "CPU合成が重く電話には不適 (1文約6秒)",
};

type State = { primary: string; fallback: string; choices: string[] };

export default function VoiceSelect() {
  const [s, setS] = useState<State | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/settings", { cache: "no-store" })
      .then((r) => r.json())
      .then((d) =>
        setS({
          primary: d.tts_primary ?? "",
          fallback: d.tts_fallback ?? "",
          choices: d.tts_choices ?? Object.keys(LABELS),
        }),
      )
      .catch(() => setErr("設定を読み込めませんでした"));
  }, []);

  const save = async (patch: Partial<Pick<State, "primary" | "fallback">>) => {
    if (!s || busy) return;
    setBusy(true);
    setErr(null);
    const body =
      "primary" in patch ? { tts_primary: patch.primary } : { tts_fallback: patch.fallback };
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await res.json();
      if (!res.ok) {
        setErr(d.error ?? "保存できませんでした");
        return;
      }
      setS({ ...s, ...patch });
    } finally {
      setBusy(false);
    }
  };

  if (err && !s) return <div className="set-note">⚠ {err}</div>;
  if (!s) return <span className="muted">読み込み中…</span>;

  const row = (
    key: "primary" | "fallback",
    title: string,
    desc: string,
    extra: { value: string; label: string }[],
  ) => (
    <div className="set-row">
      <div>
        <div className="set-row-title">{title}</div>
        <div className="set-row-desc">{desc}</div>
        {s[key] && NOTES[s[key]] && <div className="set-row-desc muted">{NOTES[s[key]]}</div>}
      </div>
      <select
        value={s[key]}
        disabled={busy}
        onChange={(e) => save({ [key]: e.target.value } as Partial<State>)}
      >
        {extra.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
        {s.choices.map((c) => (
          <option key={c} value={c}>
            {LABELS[c] ?? c}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <>
      {row("primary", "メインの声", "通話で最初に使う音声合成", [
        { value: "", label: "サーバー既定 (.env)" },
      ])}
      {row(
        "fallback",
        "サブの声",
        "メインの合成が失敗したときに自動で切り替わる先",
        [
          { value: "", label: "サーバー既定 (.env)" },
          { value: "none", label: "サブなし (無言になるリスクを受け入れる)" },
        ],
      )}
      {err && <div className="set-note">⚠ {err}</div>}
      <p className="set-note">
        変更は<b>次の通話から</b>効きます (コンテナの再起動は不要)。
        {s.primary && s.fallback && s.primary === s.fallback && (
          <>
            {" "}
            ⚠ メインとサブが同じなので、実質サブなしです。
          </>
        )}
      </p>
    </>
  );
}
