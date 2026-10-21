# OSS Media Storage Runbook

**Audience:** the operator who flips Waypost's media storage from the local disk to Aliyun OSS.
**Status of the code:** merged and covered by tests, **not** enabled. Nothing here happens until an
operator sets four environment variables in `/srv/waypost/app/.env`.

This document is the operational half of the media migration. The code half — the backend, the gate
that selects it, the thumbnail pipeline and the ephemeral generated documents — shipped first, on
purpose, because there is no staging environment and no bucket: the switch had to be a one-line `.env`
edit that reverts the same way. See
[`GO_LIVE_MIGRATION_BLOCKERS_REPORT_20261013.md`](reports/GO_LIVE_MIGRATION_BLOCKERS_REPORT_20261013.md)
for the blocker list this answers and [`DEPLOYMENT.md`](DEPLOYMENT.md) §2.12 for where the keys live.

---

## 1. What the code already guarantees

| Guarantee | Where | Why it matters at cutover |
|---|---|---|
| With none of the four keys set, `STORAGES['default']['BACKEND']` is the literal `django.core.files.storage.FileSystemStorage` string this project has always used, and `MEDIA_URL` is still `/media/` | `waypost/settings.py` (the `resolve_default_storage_backend` call) | Merging this code changed nothing on the host. The nginx `alias` blocks keep matching because nothing else was possible |
| Setting one, two or three keys raises `ImproperlyConfigured` at boot (when `DEBUG=False`) | `waypost/settings.py` | A half-finished edit cannot silently leave media on the disk and then vanish on the next rebuild |
| A whitespace-only value counts as unset | `waypost/storage.py:resolve_default_storage_backend` | Hand-edited `.env` files produce `OSS_ACCESS_KEY_SECRET=` lines; treating those as set would surface as an upload 500 |
| `oss2` is imported inside the backend, never at module scope | `waypost/storage.py:_oss2` | A box on FileSystemStorage cannot be broken by a missing or broken `oss2` install |
| Objects get `Cache-Control: max-age=604800` under `inspections/` and `max-age=2592000` elsewhere | `waypost/storage.py:media_cache_control` | These are the two `expires` numbers in `docker/nginx/waypost.conf`, transcribed so a browser's cache lifetime does not change at the cutover |
| `Content-Disposition: attachment` on inspection **deliverables** only — never on `photos/`, `signatures/`, `thumbs/` | `waypost/storage.py:is_inspection_deliverable` | A blanket `attachment` under `inspections/` would make the photo grid download instead of render |
| Media URLs are signed and absolute, and memoised per name for half the expiry | `waypost/storage.py:WaypostOSSStorage.url` | One render gets one URL per object (so upstream caching works), and a long-lived page is re-signed before the URL dies |
| Generated invoice/delivery documents are written to a temp directory and deleted when the response closes | `utils/generated_documents.py` | They never enter the bucket, so they never become billable noncurrent versions |
| Application code cannot touch media through the filesystem | `tests/test_repo_hygiene.py:MediaStorageApiTests` | `FieldFile.path` raises on a bucket backend; the CI walk removes the idiom from the tree instead of discovering it in production |

**Zero migrations.** This change adds no model field and no migration file; `manage.py
makemigrations --check --dry-run` is clean, and that is the proof, not a claim. Thumbnails are
derived by naming convention (`inspections/<store_inspection_id>/thumbs/<photo_id>.jpg`), so a photo
row needs no new column. Rolling back the cutover is therefore never a schema problem.

> ⚠️ **One deliberate divergence from nginx.** The two `expires` numbers are mirrored exactly, but
> `docker/nginx/waypost.conf` sets `add_header Content-Disposition 'attachment'` on the whole
> `/media/inspections/` location, while the backend sets it only on deliverables (`report.xlsx`,
> cover pages — not `photos/`, `signatures/` or `thumbs/`). That is not a regression to fix: browsers
> ignore `Content-Disposition` on subresources, so an `<img>` in the photo grid renders either way,
> and keeping the bucket-side header on deliverables only avoids a download prompt for anything a
> person opens directly. What you must not do is copy nginx's blanket header into a bucket rule.

---

## 2. Prerequisites

- [ ] The OSS bucket does not exist yet, and the region matches the ECS (`oss-cn-heyuan` for the
      current host). A cross-region pair still works, but every upload then pays public egress and
      the internal endpoint stops being reachable.
- [ ] Versioning will be enabled (it is what makes `DELETE` recoverable and what makes the lifecycle
      rule in §5 mandatory rather than tidy).
- [ ] The `media/` bind mount on the host is intact and backed up — the cutover copies it, it does not
      move it. Keep it until §10's verification list is green.
- [ ] Nobody is mid-inspection. The store engineers' mini program sessions hold filesystem-relative
      URLs; flip between rounds (see the ordering rule in §11).

---

## 3. Create the bucket

Console → OSS → Buckets → Create Bucket:

| Setting | Value | Reason |
|---|---|---|
| Bucket name | `waypost-media` | Appears in every media URL; it is not renameable later without rewriting `MEDIA_URL` |
| Region | Same as the ECS | Internal endpoint only resolves inside the region, and cross-region reads are billed |
| Accessibility | **Private** | This is the premise: every read goes through a signed URL. A public-read bucket would expose customer inspection photos to anyone who guesses a key |
| Versioning | **Enabled** | `FieldFile.delete()` and storage overwrite both become recoverable instead of destructive |
| Server-side encryption | OSS-managed (SSE-OSS) | No key management and no KMS request cost for a bucket that only the app writes |
| Redundancy | Local (LRS) | Media is reproducible from the store's phone only in the worst case; ZRS prices for a durability the business does not need here |
| Logging | Optional | If enabled, its own objects live in the bucket and count against storage — put them in a different bucket |

Do **not** create a `media/` folder by hand. The key prefix is part of the object name the app writes
(`OSS_MEDIA_PREFIX`, default `media`), and an empty folder is only an object whose key ends in `/`.

---

## 4. RAM user and policy (least privilege)

Create a RAM user for the application only — never use the Aliyun account's root AccessKey, and never
reuse a human's. Console → RAM → Users → Create User, access mode **OpenAPI Access Key** only, no
console password. Attach a custom policy, not a managed one:

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "oss:GetObject",
        "oss:PutObject",
        "oss:DeleteObject",
        "oss:AbortMultipartUpload",
        "oss:ListParts"
      ],
      "Resource": [
        "acs:oss:*:*:waypost-media/media/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:ListObjects"
      ],
      "Resource": [
        "acs:oss:*:*:waypost-media"
      ],
      "Condition": {
        "StringLike": {
          "oss:Prefix": [
            "media/*"
          ]
        }
      }
    }
  ]
}
```

Why this shape, and what is deliberately missing:

- The `Resource` is scoped to `waypost-media/media/*`. The prefix is in the policy because the app's
  own prefix is a convention, not a boundary: a key outside it would be writable by the credentials,
  and one bucket can hold several environments (`dev/`, `staging/`, `media/`).
- `oss:ListObjects` is a bucket-level action and cannot be scoped by path any other way — the
  `oss:Prefix` condition is what scopes it. `manage.py cleanup_orphan_photo_zips` and the report walks
  both list, so dropping this statement makes those commands fail, not uploads.
- **No `oss:DeleteBucket`, `oss:PutBucket*`, `oss:*` wildcards.** A web application that can reconfigure
  its own bucket can be talked into making it public.
- **No `oss:PutObjectAcl` / `oss:GetObjectAcl`.** The bucket is private and stays private by policy, not
  by habit.
- `AbortMultipartUpload` + `ListParts` are here because an interrupted chunked upload leaves parts that
  are billed as storage and are invisible in the object list; without the action the app can never clean
  them up. (They only matter if something other than Django writes large objects; the app buffers.)

Create the AccessKey, store secret + id in `/srv/waypost/app/.env` (mode `600`, owner `deploy`), and
record the key id in the ops knowledge base so a rotation has an owner.

---

## 5. Lifecycle rules — read this before skipping it

Two rules, both on the bucket, both configuration-only.

### 5.1 Noncurrent-version expiration (mandatory)

Console → the bucket → Data Management → Lifecycle → Create Rule:

| Field | Value |
|---|---|
| Apply to | Whole bucket (`prefix` empty, so it also covers `media/` and any future prefix) |
| Current version | **No action** — media is meant to persist |
| Noncurrent version | **Delete after 30 days** |
| Partial (incomplete multipart) upload | **Delete after 7 days** |

Versioning makes every overwrite and every delete a *noncurrent* version that is billed as ordinary
Standard storage and is **invisible in the console's file list**. Without this rule, each of those
copies is kept forever.

> ⚠️ **This is the rule that protects the cost model.** The ops knowledge base's ≈8 GB ceiling and its
> "flat for three years" storage curve assume one round of photo bytes, retained once. Every reuse of a
> historical photo (`inspections/services/historical_photos.py`) and every re-rendered derivative
> writes a second copy of bytes it already had; a `save()` over an existing key does the same. Those
> writes are correct application behaviour and bounded per source→target pair, but under versioning
> each one also creates a noncurrent version that nothing ever deletes. Omit this rule and the ceiling
> is silently invalid — nothing errors, and the evidence is a bill two to three months later.
> **This rule is the reason versioning is safe to enable at all.**

### 5.2 Bucket default-header rule

Objects written by anything *other* than Django — a console upload, `ossutil`, a backfill script, a
future migration tool — carry no `Cache-Control` at all, because the app sets headers per object at
`put_object` time only. Belt and braces:

1. In the console, check the default HTTP headers on any object you create by hand, and set
   `Cache-Control: max-age=2592000` for anything outside `media/inspections/`.
2. There is no bucket-wide "default header" switch in OSS, so the operational equivalent is: **do not
   write media objects by hand.** If you must, the two values the app uses are `max-age=604800` under
   `inspections/` and `max-age=2592000` elsewhere (`waypost/storage.py`).

If the nginx `expires` numbers ever change, both places change in the same commit — the values are
mirrored deliberately, and a divergent pair produces two different cache lifetimes depending on which
backend a box happens to be using.

---

## 6. Endpoints: internal for traffic, public for URLs

On the ECS, set the **internal** endpoint:

```ini
OSS_ENDPOINT=oss-cn-heyuan-internal.aliyuncs.com
```

- Egress through the internal endpoint is not billed as public traffic, and latency is lower; uploads
  from the app are the highest-volume writes the bucket sees.
- A phone outside the VPC cannot resolve `-internal`, so signed URLs must not use it.
  `waypost/storage.py:public_oss_host` derives the public host (`oss-cn-heyuan.aliyuncs.com`) from the
  configured endpoint and signs with it. OSS V1 signatures cover the bucket and the key, not the host,
  so this is legitimate rather than a trick.
- **Do not** set the public endpoint on the host just to make it "simpler": every upload then leaves
  the VPC and appears in the bandwidth line of the bill — the line the whole cost reduction targets.
- Off the ECS (a laptop, a CI box), use the public endpoint. The backend works identically;
  `public_oss_host` returns the host unchanged when there is no `-internal` to strip.

The rest of the keys:

```ini
OSS_BUCKET_NAME=waypost-media
OSS_ACCESS_KEY_ID=<RAM user key id>
OSS_ACCESS_KEY_SECRET=<RAM user key secret>
OSS_MEDIA_PREFIX=media
OSS_SIGNED_URL_EXPIRY_SECONDS=3600
```

`OSS_MEDIA_PREFIX=media` reproduces the URL shape the app has always had
(`…/media/inspections/…`), which keeps existing stored `FieldFile.name` values — they are all relative
to the storage root — meaningful. Setting it to empty puts keys at the bucket root; nothing else needs
to change, but the `MEDIA_URL` prefix then has no path segment to hide behind.

`OSS_SIGNED_URL_EXPIRY_SECONDS=3600` means **media URLs are temporary**. Nothing in the project
persists one: email attachments store a storage name, templates call `{{ field.url }}` per render, and
the mini program re-fetches per request. If you are about to paste a media URL into a document or a
ticket, understand it will stop working within an hour.

---

## 7. Backfill the thumbnails *before* the sync

```bash
python manage.py generate_photo_thumbnails --all --dry-run   # lists every derivative that is missing
python manage.py generate_photo_thumbnails --all
```

Thumbnails are rendered on upload, so photos stored before the pipeline have none, and every list view
falls back to the full-size original (by design — nothing is broken, it just costs 10× the bytes per
grid). Backfilling while media is still on the filesystem means the derivative is one of the objects the
sync in §8 copies, instead of a second pass that writes into the bucket over a slower link. The command
skips photos that already have a derivative, so it is safe to re-run and safe to interleave with live
uploads, and it reads and writes through `default_storage` — the same command works after the cutover.

---

## 8. Sync the existing `media/` tree

`inspections/` and every other folder under the media root must exist in the bucket *before* the flag
flips, because the flip is instant and a photo that is not in the bucket 404s for the store that took
it. Copy with `ossutil`, mirroring the same prefix the app will use:

```bash
ossutil cp -r -u \
  ~/waypost-data/media/ \
  oss://waypost-media/media/ \
  --meta "Cache-Control:max-age=2592000"
```

- `-u` (update) skips objects that already exist and are unchanged, so a second run is cheap; re-run it
  after the last writes before the flip if the gap between the two exceeds a deploy.
- The `--meta` value is the coarse version of the app's rule: everything synced gets 30 days, then any
  object the app later re-saves gets its correct per-folder value. If you would rather be exact, sync
  `media/inspections/` in its own command with `--meta "Cache-Control:max-age=604800"`.
- Verify counts before flipping: `find ~/waypost-data/media -type f | wc -l` against the
  `Object Number is: N` line `ossutil ls oss://waypost-media/media/` ends with.

Then, in the same change, set the four keys in `.env` and restart (`docs/RELEASE_PROCEDURE.md` §6).
Nothing needs a rebuild — the backend is chosen at settings import.

---

## 9. Verify the cutover

Do these in order, all of them from outside the VPC where noted. This is the checklist the plan defers
until the bucket exists; do not treat §8's sync as the verification.

1. `curl -f https://<host>/healthz/` — the app booted, which already proves the gate was satisfied and
   `oss2` imported.
2. Upload one photo through the mini program or `POST /api/v1/inspections/{id}/photos/`.
3. In the console, open the bucket and confirm **two** objects appeared: the original under
   `media/inspections/<id>/photos/` and the JPEG derivative under `media/inspections/<id>/thumbs/`.
   No thumbs object means `ensure_thumbnail` failed — check the app log, not the response; a thumbnail
   failure never fails the upload.
4. Click the object → **View Details / HTTP headers** and read `Cache-Control`. It must read
   `max-age=604800` for anything under `inspections/`. (For a bulk check rather than a click-per-object,
   `ossutil stat oss://waypost-media/media/inspections/<id>/photos/<file>` prints the same header.)
   A missing header means the object did not come from Django — see §5.2.
5. Copy the **signed URL** from an API response and `curl -i` it from a laptop. Expect `200` and a
   `Content-Length` equal to the object size. Then confirm the same URL *without* the query string
   returns `403` — that is the private-bucket assertion, and it is the one that matters.
6. `curl -I` the same signed URL for a report workbook (`…/inspections/<id>/report.xlsx`) and confirm
   `Content-Disposition: attachment`; for a photo confirm it is **absent**.
7. Load an inspection detail page in the web UI and confirm photos render inline in the grid.
8. Send one email dispatch (`TEST_OUTBOUND_EMAIL_OVERRIDE` set) and confirm every attachment arrives,
   including the invoice and delivery documents — those come from a temp directory now, not from media.
9. `ls /tmp | grep waypost-gen` after the downloads: **empty**. Generated documents are reaped when the
   response closes; a leftover directory means a closer did not run.

Run the three-entry-point regression the ops knowledge base demands for any edge change
(`ams` / `admin.helpdesk` / bare IP) before declaring the cutover done.

---

## 10. What stops applying — and what must not be deleted yet

Retiring these is a **separate, post-cutover change**. They are listed here so the change has a
checklist, not so this runbook deletes them.

| Thing | Status at cutover | When to remove |
|---|---|---|
| `location /media/inspections/` and `location /media/` in `docker/nginx/waypost.conf` | Harmless dead weight: every media URL is absolute (`https://<bucket>.<host>/…`), so these `alias` blocks simply stop matching. They stay **correct and load-bearing until the bucket exists**, and the file itself says so at `:104-112` ("KEEP THESE BLOCKS") — that comment is the reason this runbook does not delete them | Only after §9 passes and no media URL is served through nginx. Confirm with `grep -c '/media/' access.log` |
| The `setfacl` chain that makes `~/waypost-data/media` readable by `www-data` (`DEPLOYMENT.md` §2.3) | No reader: nginx no longer traverses the home directory to serve uploads, so `g:www-data:x` on `/home/deploy` and the default-ACL walk are pure attack-surface widening | With the nginx blocks, and re-check whether `/home/deploy` still needs group traversal for anything else |
| `MEDIA_ROOT` writes / the `waypost-data/media` bind mount | Still the storage root for anything running without the keys — a dev box, and the rollback target | Keep the volume and the mount. Delete nothing until the bucket has survived a backup-and-restore drill |
| `media` step in `/srv/waypost/bin/waypost-backup.sh` | Redundant: OSS versioning + the 30-day noncurrent rule replace a 28-day tar of the same bytes, and keeping both re-stores the same data on the system disk | After the lifecycle rule is confirmed attached — deleting the tar first leaves no offline copy at all |
| `/home/deploy/waypost-data/media/` contents themselves | The only surviving copy of everything §8 did not sync | After a restore drill against the bucket proves the sync was complete |

One host-level item *starts* applying at this PR, independent of the bucket: generated documents now
live in `/tmp/waypost-gen-*` and are deleted when the response closes. That closer runs in-process, so
a worker killed by `SIGKILL` mid-stream leaves a directory nothing in Django can reap. The host's own
`/tmp` ageing (`systemd-tmpfiles-clean.timer`, the interval behind `tmpfiles-clean.service`) eventually
sweeps it, which is why no new mechanism is required — but the default age is long, so make the
intention explicit:

```ini
# /etc/tmpfiles.d/waypost.conf
# Three days is orders of magnitude beyond any request, so a live download is never
# swept out from under the response.
e /tmp/waypost-gen-* - - - 3d
```

`systemd-tmpfiles --create` is not needed for an age rule; confirm the host parsed the file with
`systemd-tmpfiles --clean` and then `ls /tmp | grep waypost-gen`.

---

## 11. Ordering: the cutover must precede real inspections

This is a business constraint, not a technical one, and it is the reason §8's sync step exists.

- Photos from **before** the cutover are only in the bucket if §8 ran. Do the sync, not the flip alone.
- Photos taken **during** the flip land wherever the process that served them was configured at the
  moment of the write. A gunicorn worker that imported settings before the restart keeps writing to the
  filesystem while a newer worker writes to the bucket, so a batch interrupted mid-restart can end up
  split across two stores. Keep the flip outside inspection hours and verify a store's whole round
  renders before handing the phone back.
- Historical-photo reuse (`inspections/services/historical_photos.py`) **copies bytes** into the new
  inspection's prefix. Running it after the cutover means the copies are bucket objects, and each
  overwrite of an existing key creates a noncurrent version — which is exactly why §5.1 must be in
  place *before* the first real round, not after the first surprise bill.
- Do the cutover, then run one supervised round, then release the store engineers. A failed flip during
  a supervised round costs a re-upload; a failed flip during a live round at a store in another city
  costs an on-site visit.

Rollback is the same edit in reverse: unset the four keys, restart. Files already in the bucket are
unreachable from the filesystem view, which is why §8's sync runs *before* the flip and the local
`media/` is never deleted (see §10).

---

## 12. Cost notes for the ops knowledge base

Storage is ~0.2% of the projected monthly bill, so the numbers here are recorded to keep the estimate
honest, not to optimise:

- The `~30 KB` per thumbnail the cost model uses is what
  `inspections/services/thumbnails.py` produces from a 1920×1440 original (480 px long edge, quality
  75) — pinned by a test that asserts the derivative is under 50 KB.
- Generated invoice/delivery documents are **never** stored, so they contribute nothing to the bucket.
  That was the cheapest possible outcome of the ephemeral-`TemporaryDirectory` change.
- Noncurrent versions are the only *unbounded* term, and §5.1 bounds them to 30 days. If the KB's §2.2
  ceiling is revisited, it should be revisited with the rule in place; quoting the ceiling without the
  rule is quoting a number the configuration cannot hold.
- If a bucket-side report ever asks "how many objects": `ossutil ls` counts versions, not objects, so
  the console's **Usage** page (or `GetBucketStat`) is the number that matches the bill.

---

## Related Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — §2.3 (the ACL chain being retired), §2.4 (`.env`), §2.12 (where the
  keys are documented for deployers)
- [`RELEASE_PROCEDURE.md`](RELEASE_PROCEDURE.md) — §6 deploy, §8 rollback, §11 known gaps
- [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) — the media backup step §10 makes redundant
- [`reports/GO_LIVE_MIGRATION_BLOCKERS_REPORT_20261013.md`](reports/GO_LIVE_MIGRATION_BLOCKERS_REPORT_20261013.md)
  — blockers 1, 3, 4, 5, 8, 12 that this runbook closes
- [`API_GUIDE.md`](API_GUIDE.md) — the photo upload endpoint whose response carries signed URLs
- `waypost/storage.py`, `utils/generated_documents.py`, `inspections/services/thumbnails.py`,
  `tests/test_storage_backend.py` — the implementation and its tests

---

*Maintainer: Sean Liu (@sean7084)*
