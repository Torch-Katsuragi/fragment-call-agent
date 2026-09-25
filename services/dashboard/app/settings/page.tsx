import { auth, signOut } from "@/auth";
import { LINES, PIPELINE } from "@/lib/env";
import AssistantToggle from "@/components/AssistantToggle";
import AppDeviceSettings from "@/components/AppDeviceSettings";
import VoiceSelect from "@/components/VoiceSelect";
import PromptEditor from "@/components/PromptEditor";
import PairDevice from "@/components/PairDevice";
import UrgentCallers from "@/components/UrgentCallers";
import MonitorVolume from "@/components/MonitorVolume";
import MonitorOutsideCallToggle from "@/components/MonitorOutsideCallToggle";

export const metadata = { title: "設定 | フラグメント 管制室" };

// 設定ページ (2026-09-24 に整理: ユーザー「on/off はトグルで。文字多すぎ。グッドデザインに」)。
//
// ⚠形の決まり: 節ごとに 1 枚のカード (.set-group) に行を並べる。行は「名前 + 一行の説明」と
//   右端の操作だけ。説明は 1 行に収まらないなら削る。⚠注意書き (偽装・鍵) だけは短くして残す。
// ⚠並び: よく触るもの (応答・取り次ぎ・端末) → たまに触るもの (プロンプト) →
//   表示だけのもの (システム) → アカウント
export default async function SettingsPage() {
  const session = await auth();
  const user = session?.user;

  return (
    <div className="settings">
      <h1 className="page-title">設定</h1>

      <section className="set-section">
        <h2 className="set-heading">応答</h2>
        <div className="set-group">
          <AssistantToggle />
          <MonitorOutsideCallToggle />
          <MonitorVolume />
        </div>
      </section>

      <section className="set-section">
        <h2 className="set-heading">取り次ぎ</h2>
        <UrgentCallers />
      </section>

      <section className="set-section">
        <h2 className="set-heading">スマホアプリ</h2>
        <div className="set-group">
          {/* アプリの中で開いたときだけ「この端末」の行が出る。
              ⚠他の端末を管制室から切り替える口は置かない (2026-09-24 ユーザー「事故のもと。
              自分のサイレントだけいじれれば十分」)。沈黙は各端末が自分で切り替える */}
          <AppDeviceSettings />
          <PairDevice />
        </div>
      </section>

      <section className="set-section">
        <h2 className="set-heading">応対プロンプト</h2>
        <PromptEditor />
      </section>

      <section className="set-section">
        <h2 className="set-heading">システム</h2>
        <div className="set-group">
          {LINES.map((l) => (
            <div className="set-row inline" key={l.id}>
              <div className="set-row-text">
                <div className="set-row-title">{l.number}</div>
                <div className="set-row-desc">{l.label}</div>
              </div>
              <span className={`badge ${l.configured ? "ok" : "off"}`}>
                {l.configured ? "接続済み" : "未設定"}
              </span>
            </div>
          ))}
          <div className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">AIコア</div>
              <div className="set-row-desc">
                声 {PIPELINE.voice}
                {PIPELINE.aiCore !== "live" && ` ・ 認識 ${PIPELINE.sttProvider} ・ 合成 ${PIPELINE.ttsProvider}`}
              </div>
            </div>
            <span className="set-val-inline">{PIPELINE.aiCore}</span>
          </div>
          {/* live では声は Gemini で固定 (定型文も同系の TTS に固定)。選べるのは cascade のときだけ */}
          {PIPELINE.aiCore !== "live" && <VoiceSelect />}
        </div>
        <p className="set-foot">変更はサーバーの .env で行います (表示のみ)。</p>
      </section>

      <section className="set-section">
        <h2 className="set-heading">アカウント</h2>
        <div className="set-group">
          {user ? (
            <div className="set-row inline">
              <div className="account-row">
                {user.image && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    className="account-avatar lg"
                    src={user.image}
                    alt=""
                    referrerPolicy="no-referrer"
                  />
                )}
                <div>
                  <div className="set-row-title">{user.name}</div>
                  <div className="set-row-desc">{user.email}</div>
                </div>
              </div>
              <form
                action={async () => {
                  "use server";
                  await signOut({ redirectTo: "/login" });
                }}
              >
                <button className="btn-quiet" type="submit">
                  ログアウト
                </button>
              </form>
            </div>
          ) : (
            // アプリ (端末トークン) で開いたときは Google のセッションが無い
            <div className="set-row">
              <div className="set-row-desc">スマホアプリの端末として接続しています</div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
