import { auth, signOut } from "@/auth";
import { LINES, PIPELINE } from "@/lib/env";
import AssistantToggle from "@/components/AssistantToggle";
import VoiceSelect from "@/components/VoiceSelect";
import PromptEditor from "@/components/PromptEditor";
import UrgentCallers from "@/components/UrgentCallers";
import MonitorVolume from "@/components/MonitorVolume";
import MonitorOutsideCallToggle from "@/components/MonitorOutsideCallToggle";
import MembersSettings from "@/components/MembersSettings";
import ScheduleSettings from "@/components/ScheduleSettings";
import SelfLabelSetting from "@/components/SelfLabelSetting";
import RecordingSettings from "@/components/RecordingSettings";
import { TENANT_ID, currentTenant, tenantsOfEmail } from "@/lib/tenants";

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
  const current = currentTenant();
  const mine = user?.email ? await tenantsOfEmail(user.email) : [];

  return (
    <div className="settings">
      <h1 className="page-title">設定</h1>

      <section className="set-section">
        <h2 className="set-heading">応答</h2>
        <div className="set-group">
          <AssistantToggle />
          <SelfLabelSetting />
          <MonitorOutsideCallToggle />
          <MonitorVolume />
        </div>
      </section>

      <section className="set-section">
        <h2 className="set-heading">時間割</h2>
        <ScheduleSettings />
      </section>

      <section className="set-section">
        <h2 className="set-heading">取り次ぎ</h2>
        <UrgentCallers />
      </section>

      <section className="set-section">
        <h2 className="set-heading">メンバー</h2>
        <MembersSettings />
      </section>

      <section className="set-section">
        <h2 className="set-heading">スマホアプリ</h2>
        <div className="set-group">
          <div className="set-row">
            <div className="set-row-desc">
              アプリは Google アカウントでログインします。新しい人は上の「メンバー」から招待してください。
              1 つのアカウントで何台も入れた端末 (事務所の子機など) は、メンバー欄でその人の下に 1 台ずつ並び、
              権限の上限を付けたり 1 台だけ止めたりできます。
            </div>
          </div>
        </div>
      </section>

      <section className="set-section">
        <h2 className="set-heading">録音</h2>
        <RecordingSettings />
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
          {/* 同居構成 (2026-10-04): いまどの管制室か。複数の管制室のメンバーなら切り替えられる */}
          <div className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">管制室</div>
              <div className="set-row-desc">{current?.label ?? TENANT_ID}</div>
            </div>
            {mine.filter((t) => t.id !== TENANT_ID).map((t) => (
              <a
                key={t.id}
                className="btn-quiet"
                href={`/api/public/tenant?to=${encodeURIComponent(t.id)}&next=/`}
              >
                {t.label} へ切り替え
              </a>
            ))}
          </div>
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
