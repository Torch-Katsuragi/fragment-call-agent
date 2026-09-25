"use client";

import { useEffect, useState } from "react";

// 通話画面を開いていないときに AI 応対中の音声を流すか (2026-09-18)。
//
// ユーザーの指摘: 「AI応答中に音声を流すかは任意にしてほしい。その画面を開いていたら確定で流す、
// それ以外は設定次第。せっかくの応対自動化が台無し」。
// 管制室は CallSessionProvider が**どの画面でも**アクティブ通話のルームに繋ぎ (文字起こしと
// 「通話中」チップのため)、MonitorAudio がその音声を再生する。PC のブラウザは自動再生制限で
// 「🔊 音声オン」を押すまで鳴らないが、**アプリの WebView は自動再生を許可している**ので、
// アプリを開いただけで AI の応対音声が流れ出していた。
//
// ⚠既定は **流さない**。通話画面 (/call/<id>) を開いているときは設定に関係なく流す。
// ⚠保存先は localStorage = 端末ごと (モニタ音量と同じ理由。PC とスマホで望みが違う)。
const KEY = "fragment.monitorOutsideCall";
const EVENT = "fragment:monitorOutsideCall";

export function readMonitorOutsideCall(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(KEY) === "1";
}

export function writeMonitorOutsideCall(on: boolean) {
  window.localStorage.setItem(KEY, on ? "1" : "0");
  window.dispatchEvent(new CustomEvent(EVENT, { detail: on }));
}

/** 設定画面と「通話中」チップの両方から使う。どちらで変えても即座に揃う */
export function useMonitorOutsideCall(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(false);
  useEffect(() => {
    setOn(readMonitorOutsideCall());
    const onCustom = (e: Event) => setOn((e as CustomEvent<boolean>).detail);
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY) setOn(readMonitorOutsideCall());
    };
    window.addEventListener(EVENT, onCustom);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(EVENT, onCustom);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  return [on, writeMonitorOutsideCall];
}
