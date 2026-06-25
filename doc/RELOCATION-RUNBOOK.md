# Server Relocation Runbook — US → EU (co-locate with Backblaze B2 EU)

**Goal:** Move the szurubooru host from Contabo US to Contabo EU (Germany) so the
server sits ~5–10 ms from the B2 bucket (EU Central / Amsterdam, cluster `003`)
instead of the current **~145 ms**. This eliminates the transatlantic latency on
every cold image read and metadata lookup — the root cause of slowness.

> Measured before migration: RTT to `s3.eu-central-003.backblazeb2.com` from the
> US box = **~145 ms**. Target after migration: **~5–10 ms**.

---

## ⚠️ Critical facts before you start

1. **Contabo relocation is DESTRUCTIVE and IN-PLACE.** "A fresh instance is created
   in the target region. All existing data will be deleted." You will **not** have
   the old and new servers running at the same time on the same VPS. Therefore you
   must pull a complete off-box backup **before** triggering relocation.
2. **`server/config.yaml` `secret:` is load-bearing.** szuru uses it to (a) salt
   password hashes and (b) generate static-content/thumbnail filenames. If it
   changes, **all users are locked out and thumbnail paths break.** Carry it over
   **verbatim**. This is the single most important file.
3. **The posts (67 GB) are NOT on the server** — they live on B2 and are untouched
   by the wipe. You do **not** copy them. The new server just re-mounts the same
   bucket (now nearby).
4. **The DB is the only live state that can be lost.** It is dumped hourly to B2,
   but you will take a **final dump at cutover** for zero data loss.

---

## What actually has to move

| Item | Location (old server) | Strategy |
|------|----------------------|----------|
| Posts (67 GB) | B2 `tengu-posts` (cluster 003) | **Nothing to do** — re-mount on new box |
| Database | local `/var/local/szurubooru/sql` (PG 11) | Final `pg_dump` → restore on new box |
| `secret` + site config | `/root/szuru/server/config.yaml` | Copy verbatim |
| Compose env (DB creds, ports) | `/root/szuru/.env` | Copy verbatim |
| rclone bucket creds | `/root/.config/rclone/rclone.conf` | Copy verbatim |
| rclone mount unit | `/etc/systemd/system/rclone-booru.service` | Copy (apply tuned flags) |
| nginx TLS vhost | `/etc/nginx/sites-available/tengu-futaket.xyz` | Copy |
| TLS certs | `/etc/letsencrypt/` (certbot) | Re-issue on new box after DNS (simpler than copying) |
| DB backup script + cron | `/usr/local/bin/szuru-backup.sh`, root crontab | Copy |
| b2 CLI auth (for backups) | b2 account creds | Re-auth on new box |
| Thumbnails (627 MB) | local `/var/local/szurubooru/data/generated-thumbnails` | Copy (faster than regenerating) |
| Avatars (1.4 MB) | local `/var/local/szurubooru/data/avatars` | Copy |
| Repo | `/root/szuru` | `git clone` on new box + copy untracked configs |

---

## Phase 0 — Pre-flight (do this DAYS before, no downtime)

**0.1 — Verify the DB backup actually restores.** Do not trust an untested backup.
On the *current* server (or locally), pull the latest dump and test-restore it into
a throwaway Postgres 11 container:

```bash
# Pull latest dump from B2
LATEST=$(b2 ls tengu-posts-backup | tail -1)
b2 download-file-by-name tengu-posts-backup "$LATEST" "/tmp/$LATEST"

# Spin a scratch PG 11 and restore into it
docker run -d --name pgtest -e POSTGRES_USER=szuru -e POSTGRES_PASSWORD=test postgres:11-alpine
sleep 8
gunzip -c "/tmp/$LATEST" | docker exec -i pgtest pg_restore -U szuru -d szuru --clean --if-exists -v
# sanity check
docker exec pgtest psql -U szuru -d szuru -c "select count(*) from post;"
docker rm -f pgtest
```

If `count(*)` looks right, the backup path is trustworthy. **Do not proceed to
Phase 2 until this passes.**

**0.2 — Confirm DNS control.** Identify where `tengu-futaket.xyz` and
`direct.tengu-futaket.xyz` DNS is hosted (Cloudflare). Lower the **A-record TTL**
to 60–300s a day ahead so the cutover propagates fast. Current target IP:
`209.145.49.213` (will change).

**0.3 — Confirm Contabo EU region + price.** Pick the German region (closest to
Backblaze Amsterdam). Confirm "free relocation" applies and note expected wipe time.

---

## Phase 1 — Build the migration bundle (no downtime, on current server)

Collect everything reproducible into one tarball and push it **off the box**
(to your laptop AND/or a B2 bucket), because the relocation will erase the disk.

```bash
mkdir -p /tmp/migrate
# Configs (contain secrets — keep this bundle private)
cp /root/szuru/server/config.yaml            /tmp/migrate/
cp /root/szuru/.env                          /tmp/migrate/
cp /root/.config/rclone/rclone.conf          /tmp/migrate/
cp /etc/systemd/system/rclone-booru.service  /tmp/migrate/
cp /etc/nginx/sites-available/tengu-futaket.xyz /tmp/migrate/nginx-tengu.conf
cp /usr/local/bin/szuru-backup.sh            /tmp/migrate/
crontab -l > /tmp/migrate/root.crontab
# Local data that isn't on B2
tar -C /var/local/szurubooru/data -czf /tmp/migrate/thumbnails.tgz generated-thumbnails avatars
# Record current commit so you rebuild the same code
git -C /root/szuru rev-parse HEAD > /tmp/migrate/szuru-commit.txt
git -C /root/szuru remote -v        > /tmp/migrate/szuru-remote.txt

tar -czf /tmp/szuru-migrate-bundle.tgz -C /tmp migrate
```

Copy `/tmp/szuru-migrate-bundle.tgz` to a safe place OFF the server:
```bash
# to your laptop:
scp root@209.145.49.213:/tmp/szuru-migrate-bundle.tgz .
# and/or to B2 as a second copy:
b2 upload-file tengu-posts-backup /tmp/szuru-migrate-bundle.tgz szuru-migrate-bundle.tgz
```

> ⚠️ This bundle contains DB password, rclone keys, and the szuru `secret`. Treat
> it as a secret; delete the laptop copy once migration is verified.

---

## Phase 2 — Cutover (downtime starts here)

**2.1 — Put the site into maintenance / stop writes.** Announce downtime. Stop the
app so no new posts/edits happen after the final dump:
```bash
cd /root/szuru && docker compose stop server client
```

**2.2 — Final DB dump (zero-loss).** This captures everything since the last hourly
backup:
```bash
docker exec szuru-sql-1 pg_dump -U szuru -F c szuru | gzip > /tmp/migrate/FINAL.pgsql.gz
b2 upload-file tengu-posts-backup /tmp/migrate/FINAL.pgsql.gz szuru-FINAL.pgsql.gz
# also pull FINAL.pgsql.gz to your laptop
```
Re-tar/re-upload the bundle so it includes `FINAL.pgsql.gz`, or upload it separately
(as above). **Verify the upload succeeded** before the next step.

**2.3 — Trigger Contabo relocation.** Confirm you have the bundle + FINAL dump off
the box, then start the relocation in the Contabo panel. Note the **new IP**.

---

## Phase 3 — Rebuild on the fresh EU instance

SSH to the new box (new IP). Then:

**3.1 — Base system:**
```bash
apt update && apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx
curl -fsSL https://github.com/Backblaze/B2_Command_Line_Tool/releases/latest/download/b2-linux -o /usr/local/bin/b2 && chmod +x /usr/local/bin/b2
# rclone (match prod version 1.60.x or newer)
apt install -y rclone   # or install the same binary version as before
```

**3.2 — Restore the bundle:**
```bash
mkdir -p /var/local/szurubooru/data /var/local/szurubooru/data/posts /var/local/szurubooru/sql
mkdir -p ~/.config/rclone
tar -xzf szuru-migrate-bundle.tgz -C /tmp           # -> /tmp/migrate/...
cp /tmp/migrate/rclone.conf ~/.config/rclone/rclone.conf
cp /tmp/migrate/rclone-booru.service /etc/systemd/system/
cp /tmp/migrate/szuru-backup.sh /usr/local/bin/ && chmod +x /usr/local/bin/szuru-backup.sh
crontab /tmp/migrate/root.crontab
tar -xzf /tmp/migrate/thumbnails.tgz -C /var/local/szurubooru/data
```

**3.3 — Clone the repo at the same commit + restore configs:**
```bash
git clone <remote-from-szuru-remote.txt> /root/szuru
cd /root/szuru && git checkout $(cat /tmp/migrate/szuru-commit.txt)
cp /tmp/migrate/config.yaml server/config.yaml      # carries the load-bearing secret
cp /tmp/migrate/.env .env
```

**3.4 — Mount B2 (now nearby) with the tuned flags:**
Edit `/etc/systemd/system/rclone-booru.service` to use the improved cache flags
(see "rclone flags" section below), then:
```bash
systemctl daemon-reload
systemctl enable --now rclone-booru
ls /var/local/szurubooru/data/posts | head   # should list real files
# verify latency win:
ping -c3 s3.eu-central-003.backblazeb2.com    # expect ~5-10ms
```

**3.5 — Bring up DB + app and restore data:**
```bash
cd /root/szuru
docker compose up -d sql
sleep 10
gunzip -c /tmp/migrate/FINAL.pgsql.gz | docker exec -i szuru-sql-1 pg_restore -U szuru -d szuru --clean --if-exists -v
docker compose up -d server client
docker compose logs -f server   # watch for clean startup / migrations
```

> If szuru runs Alembic migrations on startup and the code commit matches the dump's
> schema, no migration is needed. If you intentionally upgraded szuru, let it migrate.

---

## Phase 4 — TLS + DNS + go-live

**4.1 — nginx vhost:**
```bash
cp /tmp/migrate/nginx-tengu.conf /etc/nginx/sites-available/tengu-futaket.xyz
ln -sf /etc/nginx/sites-available/tengu-futaket.xyz /etc/nginx/sites-enabled/
nginx -t
```

**4.2 — Point DNS at the new IP.** In Cloudflare, update the A records for
`tengu-futaket.xyz` and `direct.tengu-futaket.xyz` to the new EU IP. If proxied
(orange cloud), traffic flows immediately; if grey-cloud, wait for TTL.

**4.3 — Re-issue Let's Encrypt certs** (HTTP-01 needs DNS already pointing here):
```bash
certbot --nginx -d tengu-futaket.xyz -d direct.tengu-futaket.xyz
systemctl reload nginx
```

**4.4 — Update `config.yaml` `domain:` if needed.** It currently reads
`http://209.145.49.213`. Set it to the public HTTPS domain
(`https://tengu-futaket.xyz`) for correct absolute URLs, then
`docker compose restart server`.

---

## Phase 5 — Verify, then decommission

- [ ] Site loads over HTTPS, no cert warning.
- [ ] Login works (proves `secret` carried over correctly).
- [ ] Existing thumbnails render (proves thumbnails + `secret`-derived paths intact).
- [ ] Open a post that was **never** viewed before → full image loads fast (proves
      B2 mount + low EU latency).
- [ ] Upload a new post end-to-end (incl. `direct.tengu-futaket.xyz` >100MB path).
- [ ] `du -sh ~/.cache/rclone/vfs` grows over time (cache filling, not stuck at MB).
- [ ] Hourly backup cron fires: check `b2 ls tengu-posts-backup` after the next hour,
      and confirm `b2` is authenticated on the new box (`b2 get-account-info`).
- [ ] Re-raise DNS TTL back to normal.

Only after all green: nothing to decommission (the old instance is already wiped by
the relocation). Delete the secret-bearing bundle from your laptop.

---

## rclone flags for the new mount (apply during 3.4)

Even nearby, keep the cache fixes so re-reads are instant. Change vs the old unit:

| Flag | Old | New | Why |
|------|-----|-----|-----|
| `--vfs-cache-max-age` | `24h` | `9999h` | Stop age-purging; let the 50G cache fill (LRU by size) |
| `--dir-cache-time` | `60s` | `1h` | Fewer metadata lookups (single writer, polling off) |
| `--vfs-read-ahead` | — | `128M` | Faster streaming of 1GB+ videos on first read |

Keep `--vfs-cache-max-size 50G`, `--vfs-cache-mode full`, `--b2-hard-delete`, etc.

---

## Rollback / abort

- **Before 2.3 (relocation triggered):** abort is trivial — just
  `docker compose up -d` to bring the old US site back. Nothing was destroyed.
- **After 2.3:** there is no rollback to the old box (it's wiped). Recovery = rebuild
  from the bundle (Phase 3) on the new box. This is why Phase 0.1 (verified restore)
  and Phase 1 (off-box bundle) are non-negotiable.
- Keep the bundle + FINAL dump until Phase 5 is fully green.

---

## Open questions to confirm before executing

1. **DNS host** for the domains (assumed Cloudflare) and whether records are proxied.
2. **Is `direct.tengu-futaket.xyz`** a separate vhost/cert that also needs its own
   nginx server block re-created? (It has its own LE cert.)
3. **rclone version** to install on the new box — match `v1.60.1` or upgrade
   deliberately.
4. **Maintenance window length** you can tolerate (realistically 1–3h incl. wipe +
   rebuild + DB restore + cert re-issue).
</content>
