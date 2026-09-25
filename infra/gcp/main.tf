# Call-Agent GCP インフラ (M1 最小構成)
# 使い方:
#   gcloud auth application-default login
#   terraform init && terraform apply -var project_id=<PROJECT_ID>

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "asia-northeast1" # 東京 (音声レイテンシ最優先)
}
variable "zone" {
  type    = string
  default = "asia-northeast1-b"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# 静的IP (SIPのREGISTER元/メディアのために固定しておく)
resource "google_compute_address" "call_agent" {
  name = "call-agent-ip"
}

resource "google_compute_instance" "call_agent" {
  name         = "call-agent"
  machine_type = "e2-medium" # M2でAIパイプラインが載るので最初からmedium
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 50
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.call_agent.address
    }
  }

  # 初回起動時に Docker + Compose plugin をセットアップ
  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -eux
    if ! command -v docker >/dev/null; then
      apt-get update
      apt-get install -y ca-certificates curl gnupg git
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
        https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
      systemctl enable --now docker
    fi
    mkdir -p /opt/call-agent
  EOT

  tags = ["call-agent"]
}

# 回線事業者のSIP/RTP送信元 (2026-07-26に絞り込み)。
# ⚠ 5060 (LiveKit SIP) は公開しない — Asterisk仲介構成では localhost からしか使わない。
# 公開していた数分間にSIPスキャナーが内線総当たり(1001/2000/3001/4000/4001)で68件着信し、
# AIが応答してAPI消費+OOMの一因になった実績がある (開発記録 2026-07-26)
locals {
  carrier_sip_ranges = [
    "202.173.5.181/32", # ブラステル My050 (pjsip.conf の brastel-identify と一致)
    "54.172.60.0/30",   # Twilio Elastic SIP Trunking US1 signaling
    "168.86.128.0/18",  # Twilio media (RTP)
  ]
}

# 明示DENY (2026-07-26)。GCPのファイアウォールはUDPもステートフルなため、
# allowルールから外しただけでは**確立済みフローが通り続ける** — 実際にトールフラウド
# スキャナー(66.165.237.98)の着信がFW変更後も数分間続いた。優先度を上げた明示DENYで断つ。
# ⚠ これでも即座に止まらない場合があるので、VM側にも iptables で同等のDROPを入れてある
# (netfilter-persistentで永続化済み。開発記録 2026-07-26 参照)
resource "google_compute_firewall" "call_agent_deny_sip" {
  name     = "call-agent-deny-livekit-sip"
  network  = "default"
  priority = 900 # 既定のallow(1000)より優先

  deny {
    protocol = "udp"
    ports    = ["5060"] # LiveKit SIP — localhost(Asterisk仲介)からしか使わない
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["call-agent"]
}

# 管制室 (HTTPS) は誰でも到達してよい — Googleログインで保護している
resource "google_compute_firewall" "call_agent_web" {
  name    = "call-agent-web"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["call-agent"]
}

# 音声系 (SIP/RTP) は回線事業者からのみ
resource "google_compute_firewall" "call_agent" {
  name    = "call-agent-voice"
  network = "default"

  allow {
    protocol = "udp"
    ports    = ["5070"] # Asterisk (5060=LiveKit SIPは公開しない)
  }
  allow {
    protocol = "udp"
    ports    = ["10000-10500"] # RTP (SIP)
  }

  source_ranges = local.carrier_sip_ranges
  target_tags   = ["call-agent"]
}

# WebRTC (管制室のライブ音声) — クライアントIPは不定なので全開だが、
# LiveKitのトークン認証必須なので入口としては閉じている
resource "google_compute_firewall" "call_agent_webrtc" {
  name    = "call-agent-webrtc"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["7880", "7881"] # LiveKit WS/API + TCP fallback
  }
  allow {
    protocol = "udp"
    ports    = ["50000-50100"] # WebRTC media
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["call-agent"]
}

output "external_ip" {
  value = google_compute_address.call_agent.address
}

# ===== 管制室のドメイン =====
# DNSはCloud DNSに委任してここで管理する。レジストラ側のネームサーバーを
# output "sleeptree_nameservers" の4つに向ける。
# ⚠ネームサーバーを向けた後は、レジストラ側のDNSレコード設定は無効。レコードは必ずここに書く。
#   fragment = 管制室 (このVM)

resource "google_dns_managed_zone" "sleeptree" {
  name        = "sleeptree-jp"
  dns_name    = "example.com."
  description = "Nemurigi Kobo corporate domain (services under subdomains)"
}

# fragment = 管制室 (このVM)
# (旧案の app. は「どのappか」が判別できなくなるため未公開のうちに改名した)
resource "google_dns_record_set" "sleeptree_fragment" {
  managed_zone = google_dns_managed_zone.sleeptree.name
  name         = "fragment.example.com."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.call_agent.address]
}

# livekit = 管制室のブラウザがWebRTCのシグナリングを張る先 (Caddy → localhost:7880)。
# ⚠ サービスではなくインフラだが、独立ホストが必要。管制室がHTTPSなのでブラウザは平文の
#    ws:// に繋げず (SecurityError)、TLSで出すには専用ホスト+証明書が要る。2026-07-30に
#    「発信しても双方向とも無音」の原因がこれだった (詳細は infra/caddy/Caddyfile の注記)
resource "google_dns_record_set" "sleeptree_livekit" {
  managed_zone = google_dns_managed_zone.sleeptree.name
  name         = "livekit.example.com."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.call_agent.address]
}

# kokage-map = こかげマップのweb版 (Firebase Hosting、サイトID kokage-map)。
# 隠しページ運用 (リンクを張らない + Hosting側で X-Robots-Tag: noindex)。
# ⚠ カスタムドメインは Firebase Hosting 側にも登録が要る (このCNAMEだけでは繋がらない)。
#    登録は firebasehosting API の customDomains (2026-08-28 実施済み)
resource "google_dns_record_set" "sleeptree_kokage_map" {
  managed_zone = google_dns_managed_zone.sleeptree.name
  name         = "kokage-map.example.com."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["kokage-map.web.app."]
}

output "sleeptree_nameservers" {
  value = google_dns_managed_zone.sleeptree.name_servers
}
