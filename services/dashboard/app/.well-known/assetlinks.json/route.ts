import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// Android App Links (2026-09-26)。招待リンク https://<管制室>/invite/... を押すとアプリが開くように、
// このドメインとアプリ (パッケージ名 + 署名の SHA-256) を結ぶ。
// ANDROID_CERT_SHA256 にカンマ区切りで署名の指紋を並べる (開発用と Play の署名の両方を入れられる)。
// ⚠self-host で自分のビルドを使う人は、自分の署名の指紋を入れる
const PACKAGE = process.env.ANDROID_PACKAGE ?? "jp.sleeptree.fragment";
const CERTS = (process.env.ANDROID_CERT_SHA256 ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

export async function GET() {
  return NextResponse.json([
    {
      relation: ["delegate_permission/common.handle_all_urls"],
      target: { namespace: "android_app", package_name: PACKAGE, sha256_cert_fingerprints: CERTS },
    },
  ]);
}
