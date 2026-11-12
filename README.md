# UniHub

A campus marketplace: students browse a clean, categorised feed; verified student vendors run their
own storefronts instead of spamming class group chats. Flask + SQLAlchemy. UniHub supports multiple
campuses, each stored as a row in the `universities` table.

## Run it locally

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # defaults are fine for development
flask --app app init-db                   # creates tables, seeds categories + campus address options
flask --app app create-admin you@example.com "Your Name"
flask --app app run --debug
```

Verification emails print to the terminal in development (`MAIL_BACKEND=console`).

## Environment variables

Keep all credentials in the project-root `.env` file. `.env` is ignored by Git; never put secrets in Python or commit them.

| Variable | What it is / where to get it | Dev needed | Production needed |
|---|---|---:|---:|
| `SECRET_KEY` | Random Flask signing key; generate with `python -c "import secrets; print(secrets.token_hex(32))"` | No | Yes |
| `DATABASE_URL` | Database connection URL; local SQLite is the default | No | Yes |
| `PAYMENTS_MODE` | `demo` for presentation or `paystack` for real gateway | Yes | Yes (`paystack`) |
| `PAYSTACK_VERIFY_BANK` | Allow bank resolution and subaccount API calls | Yes (`false`) | Yes (`true`) |
| `PAYSTACK_SECRET_KEY` | Paystack Dashboard API key | No in demo | Yes (live) |
| `PAYSTACK_PUBLIC_KEY` | Paystack Dashboard public key | No in demo | Yes (live) |
| `COMMISSION_PERCENT` | Platform commission percentage | No (default 10) | Yes (confirm policy) |
| `PUBLIC_BASE_URL` | Public HTTPS site URL | No | Yes |
| `MAIL_BACKEND`, `MAIL_FROM` | `console` for local email; sender identity from your email provider | No | Yes |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP connection details from your email provider | No | Yes |
| `STORAGE_BACKEND` | `local` or `s3` | No (default local) | Yes |
| `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_BASE_URL` | Bucket and credentials from your S3/R2 provider; required with S3 storage | No | If using S3 |
| `SUPPORT_EMAIL` | Support inbox managed by your team | No | Recommended |

Tests (each prints `ALL ... PASSED`):

```
python smoke_test.py        # signup -> verification -> application -> approval -> listings
python test_catalog.py      # feed, filters, search, contact rules
python test_moderation.py   # reports, removal, vendor revocation
python test_services.py     # production config, email, photo storage, headers (no database)
python test_stock.py        # product stock behavior
python test_accounts.py     # accounts, hostel addresses, photos, banking, settings and privacy
python test_notifications_admin_banners.py # notifications, admin dashboard and banners
python test_storefront.py   # storefront, cart shell and campus scoping
python test_payments.py     # cart, checkout, Paystack callbacks and orders (fake Paystack; no network)
node test_price_input.js    # live price formatting
```

## Accounts and campus addresses

Buyer signup collects a delivery hostel, block or floor, and room. Vendor signup collects the same address and password before sending a confirmation email; opening the link only previews the confirmation page. The user account is created only after pressing **Confirm my email**.

Profile settings let buyers and vendors update their profile, address, password, and appearance. Vendors can also edit their business details and securely replace bank information. Hostel choices are seeded in `services/hostels.py`; confirm the listed floor counts and add the girls' old-hostel block names before launch. Bank codes are a starting list in `services/banks.py` and should be verified against Paystack's List Banks API.

The default database is `unihub.db`. After pulling changes that add or alter tables, delete and recreate your local development database (`rm campus_market.db unihub.db`, then `flask --app app init-db`) and create the admin again.

## How it works

| Who | Can do |
|---|---|
| Buyer | Sign up with any email, browse, search, use cart and checkout, track orders, report listings |
| Vendor | Sign up with a **school email** (password and hostel address entered first; email confirmation creates the account), apply with a profile photo and bank details, get approved, list products/services with photos |
| Admin | Approve/reject vendor applications, review reports, remove listings, revoke vendors. Only for their own campus. Created with `flask --app app create-admin`, never by signup |

Vendors are also buyers, so they can buy from each other. Vendor phone numbers are collected for campus administration and are not shown to buyers.

## Paystack checkout

For local presentation testing, set `PAYMENTS_MODE=demo` and `PAYSTACK_VERIFY_BANK=false` in the project-root `.env`. Sign up or log in as a buyer, add an item to the cart, open Cart → Checkout, then click **Simulate successful payment** or **Simulate failed payment**. Demo orders never call Paystack or move money. Never use demo mode in production.

Before going live: set `PAYMENTS_MODE=paystack`, `PAYSTACK_VERIFY_BANK=true`, add live keys, then re-approve or run the payout-account retry for every vendor created with mock data. `COMMISSION_PERCENT` defaults to 10 and accepts 0–50; `MAX_VENDORS_PER_ORDER` defaults to 5. A checkout splits each vendor's net share directly to that vendor's Paystack subaccount; UniHub never holds vendor proceeds. The buyer pays only the listed item prices, and Paystack fees are borne by the main account.

For a local webhook test, run the app and expose it with ngrok http 5000 (or a similar HTTPS tunnel). Set the Paystack dashboard webhook URL to https://<tunnel>/webhooks/paystack. Use Paystack's published test cards and test bank accounts; no live credentials should be used in local development.

Before going live, use live keys, confirm split limits (maximum subaccounts, remainder handling, and minimums) and fee pricing with Paystack in writing, confirm with Paystack and a lawyer that split payments suit a campus marketplace, and check that the commission covers payment fees. **TODO:** Paystack's split documentation does not clearly specify the maximum number of subaccounts, remainder handling, or minimum share; confirm these details before launch.

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
