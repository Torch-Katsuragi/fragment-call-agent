"use client";

// オン/オフだけの設定に使うスイッチ (2026-09-24 ユーザー「on/off だけのものはトグルでいい」)。
// ⚠アニメーションは付けない (作業用の画面は即時に切り替える)
export default function Toggle({
  on,
  onChange,
  label,
  disabled = false,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  /** 読み上げ用。見た目のラベルは行のタイトルが担う */
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`switch ${on ? "on" : ""}`}
      disabled={disabled}
      onClick={() => onChange(!on)}
    />
  );
}
