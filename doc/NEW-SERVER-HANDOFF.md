# NEW SERVER HANDOFF — read me first

> **You are Claude Code on a freshly-relocated EU server with NO prior memory.**
> This document is the single source of truth for the szurubooru US→EU migration.
> It travels with the repo (`doc/NEW-SERVER-HANDOFF.md`). If anything here conflicts
> with your assumptions, trust this doc + what you observe on the box, and ask the
> user before destructive actions.
>
> **Keep this file updated** if the plan or facts change — it is the only thing that
> survives between servers.

---

## 0. Operating rules (from the project CLAUDE.md)

- This is a **live instance** with git + Backblaze backups. You MAY change the live
  system **but must warn the user immediately beforehand**: state exactly what
  you'll change, the impact, and how to roll it back. Keep changes reversible.
- The one truly irreplaceable thing on this box is the **Postgres database**.
  Everything else is reproducible (posts are on B2, configs are in the bundle).

---

## 1. Why this migration exists (the whole point)

The site stores full-size posts on **Backblaze B2**, mounted via **rclone** (FUSE).
The B2 bucket is in **EU Central / Amsterdam** (cluster `003`). The old server was in
the **US**, so every cold image read crossed the Atlantic at **~145 ms RTT** — the
root cause of slowness. This server is now in the **EU (Germany)**, ~5–10 ms from the
bucket. That latency fix is the entire goal; verify it actually happened (Phase V).

---

## 2. Architecture (what you're rebuilding)

- **Backend**: custom WSGI app, `server/`, runs in Docker (`szuru-server-1`).
- **Frontend**: vanilla-JS SPA, `client/`, Docker (`szuru-client-1`), exposes
  `127.0.0.1:8080`.
- **DB**: `postgres:11-alpine` (`szuru-sql-1`), data at `/var/local/szurubooru/sql`.
- **Images**:
  - `/var/local/szurubooru/data/posts` → **rclone mount of B2 `b2booru:tengu-posts`**
    (67 GB, the only thing on B2). Managed by systemd unit `rclone-booru.service`.
  - `/var/local/szurubooru/data/generated-thumbnails` (~627 MB), `avatars`,
    `temporary-uploads` → **local disk** (restored from the bundle).
- **TLS / proxy**: host **nginx** terminates HTTPS and proxies to `127.0.0.1:8080`.
  One vhost file `tengu-futaket.xyz` serves `tengu-futaket.xyz`, `www.`, and
  `direct.tengu-futaket.xyz`. Certs via Let's Encrypt (restored from the bundle).
- **Backups**: `/usr/local/bin/szuru-backup.sh` (hourly cron) `pg_dump`s the DB to
  B2 bucket `tengu-posts-backup`. b2 CLI auth is `/root/.b2_account_info`.

---

## 3. ⚠️ Load-bearing facts — do not get these wrong

1. **`server/config.yaml` `secret:` must be the SAME value as the old box.** szuru
   uses it to (a) salt password hashes and (b) generate static-content/thumbnail
   filenames. If it changes, **every user is locked out and thumbnails 404**. The
   bundle's `config.yaml` already has the correct secret — just restore it verbatim
   (the restore script does this). Never regenerate it.
2. **Do NOT copy the 67 GB of posts** — they live on B2, untouched. You only mount
   the bucket.
3. **DB restore is proven.** A dry-run on the old box restored cleanly: ~25,700
   posts, 2,317 tags, 12 users, 488 pools. Schema head `d60550a53dc0` (a merge
   migration = the single true head) matches the code, so `alembic upgrade head`
   (run by `docker-start.sh` on server startup) is a **no-op**.
4. **rclone cache was the original perf bug**: the old unit used
   `--vfs-cache-max-age 24h`, which age-purged the cache so it never filled (3 MB
   used out of 50 GB). The new unit (in `migrate-restore.sh`) uses
   `--vfs-cache-max-age 9999h` + `--dir-cache-time 1h` + `--vfs-read-ahead 128M`.
   Keep those.

---

## 4. The migration bundle (your inputs)

Produced by `doc/migrate-export.sh` on the old box. Find it in one of:
- The user's laptop (they `scp`'d it), **or**
- B2: `b2 ls tengu-posts-backup | grep szuru-migrate-bundle` then
  `b2 download-file-by-name tengu-posts-backup <name> ./<name>`.

It contains: `config.yaml` (with secret), `env`, `rclone.conf`, `b2_account_info`,
the nginx vhost, `szuru-backup.sh`, `root.crontab`, `thumbnails.tgz`,
`letsencrypt.tgz`, `FINAL.pgsql.gz` (the cutover DB dump), `versions.txt`,
`git-commit.txt`, `git-remote.txt`, `MANIFEST.txt`.

> The bundle holds the DB password, rclone keys and the szuru `secret`. Treat as
> secret; the user should delete laptop/B2 copies once migration is verified.

---

## 5. Rebuild procedure

Almost everything is automated. **Warn the user, then run:**

```bash
# get the repo first (this box is keyless; the repo is PUBLIC, so clone over HTTPS):
git clone https://github.com/enzof2003/szurubooru.git /root/szuru
chmod +x /root/szuru/doc/migrate-restore.sh
# the cutover bundle is in B2 (and on the user's laptop). To pull from B2:
#   b2 download-file-by-name tengu-posts-backup szuru-migrate-bundle-20260625-2246.tgz ./bundle.tgz
/root/szuru/doc/migrate-restore.sh /path/to/szuru-migrate-bundle-YYYYMMDD-HHMM.tgz
```
> The script also auto-normalizes the bundle's git remote to HTTPS, so its internal
> clone works on a keyless box too. Clone master (default branch) to get the latest
> version of this script.

The script: installs deps → restores configs/creds/thumbnails/certs → clones repo at
the recorded commit → installs the tuned rclone mount → brings up Postgres and
restores the DB → brings up server+client → enables the nginx vhost → local smoke
test. It then prints the **manual steps** below.

### Manual steps the script can't do
1. **DNS** — in Cloudflare, point `tengu-futaket.xyz`, `www.`, and
   `direct.tengu-futaket.xyz` A records at **this server's IP** (`hostname -I`).
2. **`config.yaml` `domain:`** — the old value referenced the old IP
   (`http://209.145.49.213`). Set it to `https://tengu-futaket.xyz`, then
   `cd /root/szuru && docker compose restart server`.
3. **Certs** — they were restored from the bundle and should just work. Only if
   they fail: `certbot --nginx -d tengu-futaket.xyz -d www.tengu-futaket.xyz -d direct.tengu-futaket.xyz`.

---

## Phase V — Verification checklist (don't declare done until all green)

- [ ] `systemctl status rclone-booru` active; `ls /var/local/szurubooru/data/posts` lists files.
- [ ] **Latency win confirmed**: `ping -c3 s3.eu-central-003.backblazeb2.com` ≈ 5–10 ms
      (was ~145 ms in the US). This is the whole reason for the migration.
- [ ] Site loads over HTTPS, no cert warning.
- [ ] **Login works** (proves `secret` carried over).
- [ ] Existing **thumbnails render** (proves thumbnails + secret-derived paths intact).
- [ ] Open a post **never viewed before** → full image loads fast (proves B2 mount + low latency).
- [ ] Upload a new post end-to-end, incl. the `direct.` >100 MB path.
- [ ] `du -sh ~/.cache/rclone/vfs` grows over time (cache now actually fills).
- [ ] `b2 get-account-info` works; after the next hour, a new dump appears in
      `b2 ls tengu-posts-backup` (hourly backup cron alive).
- [ ] Lower DNS TTL back to normal once stable.

---

## Rollback

- The old US server was wiped by the in-place relocation, so there is no
  flip-back-to-old option. Recovery = re-run the rebuild from the bundle on this box.
- If the DB restore looks wrong, the bundle's `FINAL.pgsql.gz` (and hourly dumps in
  `tengu-posts-backup`) can be re-restored:
  `gunzip -c FINAL.pgsql.gz | docker exec -i szuru-sql-1 pg_restore -U szuru -d szuru --clean --if-exists --no-owner`

---

## Changelog (update this when the plan changes)

- 2026-06-26 — Initial handoff written on the old US box. Migration approach: free
  Contabo in-place relocation; DB restore dry-run verified clean; tuned rclone cache
  flags adopted; bundle/scripts created (`migrate-export.sh`, `migrate-restore.sh`).
- 2026-06-26 — **EU box rebuilt and live.** Ran `migrate-restore.sh` on the new
  Contabo box (IP `217.76.48.159`, Ubuntu 24.04.4). DB restored clean (25,700 posts,
  2,317 tags, 12 users, 488 pools; alembic head `d60550a53dc0`, no migration applied).
  rclone mount up; **latency to bucket = 10 ms** (was ~145 ms — goal achieved). DNS
  cut over in **Cloudflare** (not Namecheap — Namecheap only delegates NS to
  Cloudflare): apex/www proxied (origin A → new IP), `direct.` DNS-only → new IP.
  Site, login, thumbnails, cold B2 reads all verified. Three post-rebuild fixes, now
  folded into the scripts/repo:
  1. **`temporary-uploads` dir** wasn't in the bundle → uploads 500'd
     (`PermissionError`), which the browser showed as a *misleading CORS error*.
     `migrate-restore.sh` now creates it (owned 1000:1000). **This is the #1 gotcha.**
  2. **Upload CORS**: a failed upload looks like a CORS error only because the szuru
     *client container* nginx adds `Access-Control-Allow-Origin` solely on success
     (no `always`); error responses lack it. Do **not** add CORS headers in the host
     vhost's `/api/uploads` proxied path — the client container already adds exactly
     one, and a second causes a duplicate-ACAO browser rejection. Host vhost should
     keep CORS only in the `OPTIONS` preflight block (bundle default is correct).
  3. **Upload size cap raised 1 GiB → 5 GiB**: `client/nginx.conf.docker`
     (`client_max_body_size`) + `server/docker-start.sh` (waitress
     `--max-request-body-size`). Host vhost was already `10g`. Rebuild client+server.
- 2026-06-26 — Cutover done on the old box. Cutover bundle
  `szuru-migrate-bundle-20260625-2246.tgz` validated from B2: all artifacts present,
  `secret` present, git commit `a14f32d4` matches, both domains' LE certs present,
  DB dump restores clean (25,700 posts, full schema + FKs). App confirmed stopped
  (server/client down, sql up). Fixed a keyless-clone gap: bundle captured the SSH
  remote, but the fresh box has no key — `migrate-restore.sh` now normalizes the
  GitHub remote to anonymous HTTPS (repo is public). Old server IP was 209.145.49.213.
