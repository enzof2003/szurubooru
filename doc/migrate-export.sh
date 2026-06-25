#!/bin/bash
# =============================================================================
# migrate-export.sh  —  run on the CURRENT (US) server
# Builds a self-contained "migration bundle" of everything the new EU server
# needs, plus a DB dump, and uploads it to B2 so it survives the wipe.
#
# Usage:
#   ./migrate-export.sh dryrun    # build+upload bundle WITHOUT stopping the site
#                                 # (for rehearsing; DB dump is consistent but the
#                                 #  app keeps running so later edits won't be in it)
#   ./migrate-export.sh cutover   # THE REAL RUN: stops the app, takes a final
#                                 # zero-loss DB dump, bundles, uploads. Downtime
#                                 # starts here.
#
# After a successful 'cutover' run: trigger the Contabo relocation, then on the
# fresh EU box run migrate-restore.sh with the bundle. See NEW-SERVER-HANDOFF.md.
# =============================================================================
set -euo pipefail

MODE="${1:-}"
if [[ "$MODE" != "dryrun" && "$MODE" != "cutover" ]]; then
  echo "Usage: $0 dryrun|cutover"; exit 1
fi

# --- paths / constants (current server) ---------------------------------------
SZURU_DIR="/root/szuru"
DATA_DIR="/var/local/szurubooru/data"
SQL_CONTAINER="szuru-sql-1"
DB_USER="szuru"
DB_NAME="szuru"
B2_BACKUP_BUCKET="tengu-posts-backup"
STAGE="/tmp/migrate"
BUNDLE="/tmp/szuru-migrate-bundle.tgz"

echo "=== [1/6] staging configs into $STAGE ==="
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$SZURU_DIR/server/config.yaml"            "$STAGE/config.yaml"        # LOAD-BEARING secret
cp "$SZURU_DIR/.env"                          "$STAGE/env"
cp /root/.config/rclone/rclone.conf           "$STAGE/rclone.conf"
cp /etc/systemd/system/rclone-booru.service   "$STAGE/rclone-booru.service.orig"
cp /etc/nginx/sites-available/tengu-futaket.xyz "$STAGE/nginx-tengu-futaket.xyz.conf"
cp /usr/local/bin/szuru-backup.sh             "$STAGE/szuru-backup.sh"
cp /root/.b2_account_info                     "$STAGE/b2_account_info"     # b2 CLI auth for backups
crontab -l > "$STAGE/root.crontab" 2>/dev/null || echo "# (no crontab)" > "$STAGE/root.crontab"

echo "=== [2/6] capturing versions + git state (for parity) ==="
{
  echo "exported_at=$(date -u +%FT%TZ)  mode=$MODE"
  . /etc/os-release && echo "os=$PRETTY_NAME"
  echo "docker=$(docker --version 2>/dev/null)"
  echo "compose=$(docker compose version 2>/dev/null | head -1)"
  echo "rclone=$(rclone version 2>/dev/null | head -1)"
  echo "postgres_image=postgres:11-alpine"
  echo "old_server_ip=$(hostname -I | awk '{print $1}')"
} > "$STAGE/versions.txt"
git -C "$SZURU_DIR" rev-parse HEAD            > "$STAGE/git-commit.txt"
git -C "$SZURU_DIR" remote get-url origin     > "$STAGE/git-remote.txt"

echo "=== [3/6] tarring local data not on B2 (thumbnails, avatars) + TLS certs ==="
tar -C "$DATA_DIR" -czf "$STAGE/thumbnails.tgz" generated-thumbnails avatars
tar -C /etc -czf "$STAGE/letsencrypt.tgz" letsencrypt   # valid certs -> no certbot chicken/egg

if [[ "$MODE" == "cutover" ]]; then
  echo "=== [4/6] CUTOVER: stopping app so no writes are lost after the dump ==="
  ( cd "$SZURU_DIR" && docker compose stop server client )
else
  echo "=== [4/6] dryrun: leaving app running (dump is a live consistent snapshot) ==="
fi

echo "=== [5/6] dumping database -> FINAL.pgsql.gz ==="
docker exec "$SQL_CONTAINER" pg_dump -U "$DB_USER" -F c "$DB_NAME" | gzip > "$STAGE/FINAL.pgsql.gz"
ls -lh "$STAGE/FINAL.pgsql.gz"

echo "=== [6/6] building + uploading bundle ==="
{
  echo "szurubooru migration bundle"; cat "$STAGE/versions.txt"
  echo "git_commit=$(cat "$STAGE/git-commit.txt")"
  echo "contents:"; ls -1 "$STAGE"
} > "$STAGE/MANIFEST.txt"
tar -czf "$BUNDLE" -C /tmp migrate
echo "bundle: $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"

# upload to B2 (survives the wipe). Name carries the mode + timestamp.
BUNDLE_NAME="szuru-migrate-bundle-$(date -u +%Y%m%d-%H%M).tgz"
b2 upload-file "$B2_BACKUP_BUCKET" "$BUNDLE" "$BUNDLE_NAME"

echo
echo "================================================================"
echo "DONE ($MODE). Bundle uploaded to B2 as: $BUNDLE_NAME"
echo "ALSO copy it off-box to your laptop now:"
echo "  scp root@$(hostname -I | awk '{print $1}'):$BUNDLE ."
echo
if [[ "$MODE" == "cutover" ]]; then
  echo "App is STOPPED. Once you've confirmed the bundle is safely off-box:"
  echo "  -> trigger the Contabo in-place relocation."
  echo "  -> on the fresh EU box, run migrate-restore.sh (see NEW-SERVER-HANDOFF.md)."
else
  echo "This was a DRYRUN. App is still running. Re-run with 'cutover' for the real move."
fi
echo "NOTE: this bundle contains the DB password, rclone keys and the szuru"
echo "'secret'. Treat it as a secret; delete laptop copy after migration is verified."
echo "================================================================"
