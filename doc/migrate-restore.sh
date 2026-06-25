#!/bin/bash
# =============================================================================
# migrate-restore.sh  —  run on the NEW (EU) server, fresh from the wipe
# Rebuilds the szurubooru host from a bundle produced by migrate-export.sh.
# Idempotent-ish: safe to re-run if a step fails (it recreates state).
#
# Usage:
#   ./migrate-restore.sh /path/to/szuru-migrate-bundle-YYYYMMDD-HHMM.tgz
#
# After it finishes it prints the MANUAL steps it cannot do (DNS flip + verify).
# Full context: NEW-SERVER-HANDOFF.md  (lives next to this script in the repo).
# =============================================================================
set -euo pipefail

BUNDLE="${1:-}"
[[ -f "$BUNDLE" ]] || { echo "Usage: $0 /path/to/szuru-migrate-bundle-*.tgz"; exit 1; }

# --- constants (must match the old box) ---------------------------------------
SZURU_DIR="/root/szuru"
DATA_DIR="/var/local/szurubooru/data"
SQL_DIR="/var/local/szurubooru/sql"
MOUNT="$DATA_DIR/posts"
SQL_CONTAINER="szuru-sql-1"
DB_USER="szuru"; DB_NAME="szuru"
STAGE="/tmp/migrate"

echo "=== [1/9] extracting bundle ==="
rm -rf "$STAGE"; tar -xzf "$BUNDLE" -C /tmp   # -> /tmp/migrate/*
cat "$STAGE/MANIFEST.txt"; echo

echo "=== [2/9] installing dependencies (Ubuntu 24.04 expected) ==="
. /etc/os-release; echo "this box: $PRETTY_NAME"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx rclone git fuse3
# b2 CLI
if ! command -v b2 >/dev/null; then
  curl -fsSL https://github.com/Backblaze/B2_Command_Line_Tool/releases/latest/download/b2-linux \
    -o /usr/local/bin/b2 && chmod +x /usr/local/bin/b2
fi
systemctl enable --now docker

echo "=== [3/9] restoring configs / creds ==="
mkdir -p ~/.config/rclone "$DATA_DIR" "$MOUNT" "$SQL_DIR"
cp "$STAGE/rclone.conf"      ~/.config/rclone/rclone.conf
cp "$STAGE/b2_account_info"  /root/.b2_account_info
cp "$STAGE/szuru-backup.sh"  /usr/local/bin/szuru-backup.sh && chmod +x /usr/local/bin/szuru-backup.sh
crontab "$STAGE/root.crontab"
tar -xzf "$STAGE/thumbnails.tgz"  -C "$DATA_DIR"      # generated-thumbnails + avatars
tar -xzf "$STAGE/letsencrypt.tgz" -C /etc            # valid certs

echo "=== [4/9] cloning szuru at the recorded commit + restoring app config ==="
if [[ ! -d "$SZURU_DIR/.git" ]]; then
  git clone "$(cat "$STAGE/git-remote.txt")" "$SZURU_DIR"
fi
git -C "$SZURU_DIR" fetch --all
git -C "$SZURU_DIR" checkout "$(cat "$STAGE/git-commit.txt")"
cp "$STAGE/config.yaml" "$SZURU_DIR/server/config.yaml"   # carries the LOAD-BEARING secret
cp "$STAGE/env"         "$SZURU_DIR/.env"

echo "=== [5/9] installing the rclone B2 mount (with TUNED cache flags) ==="
cat > /etc/systemd/system/rclone-booru.service <<'UNIT'
[Unit]
Description=Rclone mount for szurubooru posts
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/rclone mount b2booru:tengu-posts /var/local/szurubooru/data/posts \
  --allow-other \
  --vfs-cache-mode full \
  --vfs-cache-max-size 50G \
  --vfs-cache-max-age 9999h \
  --dir-cache-time 1h \
  --vfs-read-ahead 128M \
  --poll-interval 0 \
  --vfs-write-back 5s \
  --daemon-timeout=0 \
  --b2-hard-delete \
  --transfers 4 \
  --checkers 8 \
  --buffer-size 32M
ExecStop=/bin/fusermount -u /var/local/szurubooru/data/posts
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
UNIT
# allow_other needs this in /etc/fuse.conf
grep -q '^user_allow_other' /etc/fuse.conf 2>/dev/null || echo 'user_allow_other' >> /etc/fuse.conf
systemctl daemon-reload
systemctl enable --now rclone-booru
sleep 2
echo "--- mount check (should list real files) ---"; ls "$MOUNT" | head -3
echo "--- latency to bucket region (expect ~5-10ms in EU) ---"
ping -c3 s3.eu-central-003.backblazeb2.com 2>/dev/null | tail -2 || true

echo "=== [6/9] bringing up Postgres + restoring the database ==="
cd "$SZURU_DIR"
docker compose up -d sql
echo "waiting for postgres..."
until docker exec "$SQL_CONTAINER" pg_isready -U "$DB_USER" -q 2>/dev/null; do sleep 1; done
gunzip -c "$STAGE/FINAL.pgsql.gz" | docker exec -i "$SQL_CONTAINER" \
  pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner
echo "--- row sanity ---"
docker exec "$SQL_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t \
  -c "select 'posts='||count(*) from post;"

echo "=== [7/9] bringing up server + client ==="
docker compose up -d server client
sleep 5
docker compose ps

echo "=== [8/9] enabling nginx vhost (certs already restored) ==="
cp "$STAGE/nginx-tengu-futaket.xyz.conf" /etc/nginx/sites-available/tengu-futaket.xyz
ln -sf /etc/nginx/sites-available/tengu-futaket.xyz /etc/nginx/sites-enabled/tengu-futaket.xyz
nginx -t && systemctl reload nginx

echo "=== [9/9] local smoke test (bypassing DNS) ==="
curl -sk -H 'Host: tengu-futaket.xyz' https://127.0.0.1/ -o /dev/null -w 'local https status: %{http_code}\n' || true

NEW_IP="$(hostname -I | awk '{print $1}')"
cat <<EOF

================================================================
REBUILD COMPLETE. Remaining MANUAL steps (cannot be automated here):

1. DNS: in Cloudflare, point A records to this server's IP: $NEW_IP
     - tengu-futaket.xyz, www.tengu-futaket.xyz, direct.tengu-futaket.xyz
2. config.yaml 'domain:' currently may point at the OLD IP. Set it to
   https://tengu-futaket.xyz then: cd $SZURU_DIR && docker compose restart server
3. If certs ever fail (they were copied, should be fine), re-issue once DNS is live:
     certbot --nginx -d tengu-futaket.xyz -d www.tengu-futaket.xyz -d direct.tengu-futaket.xyz
4. Verify b2 backups still auth on this box:  b2 get-account-info

THEN run the verification checklist in NEW-SERVER-HANDOFF.md (Phase V).
================================================================
EOF
