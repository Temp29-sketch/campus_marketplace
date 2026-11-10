# Campus Market

A campus marketplace: students browse a clean, categorised feed; verified student vendors run their
own storefronts instead of spamming class group chats. Flask + SQLAlchemy. Launching at Bingham
University; every campus is just a row in the `universities` table.

## Run it locally

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # defaults are fine for development
flask --app app init-db                   # creates tables, seeds categories + Bingham
flask --app app create-admin you@example.com "Your Name"
flask --app app run --debug
```

Verification emails print to the terminal in development (`MAIL_BACKEND=console`).

Tests (each prints `ALL ... PASSED`):

```
python smoke_test.py        # signup -> verification -> application -> approval -> listings
python test_catalog.py      # feed, filters, search, contact rules
python test_moderation.py   # reports, removal, vendor revocation
python test_services.py     # production config, email, photo storage, headers (no database)
```

## How it works

| Who | Can do |
|---|---|
| Buyer | Sign up with any email, browse, search, contact sellers, report listings |
| Vendor | Sign up with a **school email** (verified by link), apply, get approved, list products/services with photos |
| Admin | Approve/reject vendor applications, review reports, remove listings, revoke vendors. Only for their own campus. Created with `flask create-admin`, never by signup |

Vendors are also buyers, so they can buy from each other. Phone numbers and WhatsApp buttons are shown to
signed-in students only (`REQUIRE_LOGIN_FOR_CONTACT` in `config.py`).

## Before the first deploy (one-time, on your computer)

The repo has no `migrations/` folder yet, and the deploy steps below need one. Generate it against a
**PostgreSQL** database (a free local or hosted one is fine) so the schema matches production:

```
export DATABASE_URL=postgresql://user:pass@localhost:5432/campus_market
flask --app app db init
flask --app app db migrate -m "initial schema"
flask --app app db upgrade                # applies it, to prove it works
git add migrations && git commit -m "Add initial migration"
```

Open `migrations/versions/*.py` and skim it once. After this, every model change is
`flask db migrate -m "what changed"` + commit + redeploy (the deploy applies it automatically).

## Deploy

Works on any host that runs Python or Docker (Render, Railway, Fly.io, a VPS). You need four things:

1. **PostgreSQL**: set `DATABASE_URL`.
2. **Photo storage**: many hosts wipe local disk on every deploy, so use an S3-compatible bucket
   (Cloudflare R2 has a free tier). Create a bucket, make it publicly readable (or attach a public
   domain), create an API token, then set `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL`,
   `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_BASE_URL`.
3. **Email**: any SMTP provider (Brevo, Resend, Mailgun, SES). Set `MAIL_BACKEND=smtp`, `MAIL_FROM`,
   `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`. Verify your sending domain with the
   provider, or verification emails will land in spam. Check this by signing up as a vendor with a real
   `@binghamuni.edu.ng` address; some university mail systems filter unknown senders.
4. **Secrets**: `APP_ENV=production`, `SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`),
   `PUBLIC_BASE_URL=https://yourdomain.com`.

`APP_ENV=production` makes the app **refuse to start** if any of these is missing or unsafe, with a message
saying what to fix. See `.env.example` for every variable.

The `Procfile` / `Dockerfile` run `flask db upgrade` and `flask seed` before starting gunicorn. Then create your
first admin once, from the host's shell:

```
flask --app app create-admin you@binghamuni.edu.ng "Your Name"
```

Point the host's uptime check at `/healthz` (returns 200 only when the database answers).

### Running more than one worker or server

Rate-limit counters live in memory by default, so each worker counts separately. With more than one
worker, set `RATELIMIT_STORAGE_URI=redis://...` (a free Redis works).

## Launch checklist

- [ ] `migrations/` generated and committed
- [ ] A test signup, vendor application, approval, listing with photos, and report all work on the live site
- [ ] Verification email arrives (check spam) for a real school address
- [ ] Photos still show after a redeploy (proves bucket storage works)
- [ ] Database backups switched on at your database host
- [ ] `/healthz` monitored
- [ ] Privacy note and campus rules page written (what's banned, how reports are handled)

## Known limits

- Deleting a listing also deletes reports filed against it, so a vendor can erase evidence by deleting a
  reported listing. Fine for launch; keep an eye on repeat offenders.
- A revoked vendor can re-apply right away; an admin must approve them again.
- Existing local photos are not moved when you switch to bucket storage: copy the `uploads/` folder into the bucket.
