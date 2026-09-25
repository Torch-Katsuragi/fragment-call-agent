# .env の値を pjsip.conf.template に埋めて pjsip.conf を生成する
# (生成後の pjsip.conf は SIP 認証情報を含むため .gitignore 対象)
$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$envFile = Join-Path $repoRoot ".env"
if (-not (Test-Path $envFile)) { throw ".env が見つかりません: $envFile" }

$vars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $vars[$Matches[1]] = $Matches[2].Trim() }
}
foreach ($required in @(
    "BRASTEL_SIP_USER", "BRASTEL_SIP_PASSWORD", "BRASTEL_SIP_SERVER",
    "TWILIO_SIP_USER", "TWILIO_SIP_PASSWORD", "TWILIO_TERMINATION_URI"
)) {
    if (-not $vars[$required]) { throw ".env に $required が設定されていません" }
}

# NAT越え用: 実行時点の公開IPを自動取得 (自宅回線は動的IPのため毎回取り直す)
$publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 5).Trim()
if ($publicIp -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw "公開IPの取得に失敗しました: $publicIp" }
$vars["PUBLIC_IP"] = $publicIp
Write-Host "公開IP: $publicIp"

$tpl = Get-Content (Join-Path $PSScriptRoot "pjsip.conf.template") -Raw
foreach ($k in $vars.Keys) { $tpl = $tpl.Replace('${' + $k + '}', $vars[$k]) }
if ($tpl -match '\$\{[A-Z_]+\}') { throw "未解決のプレースホルダが残っています: $($Matches[0])" }

Set-Content (Join-Path $PSScriptRoot "pjsip.conf") $tpl -Encoding utf8
Write-Host "pjsip.conf を生成しました (git 管理外)"
