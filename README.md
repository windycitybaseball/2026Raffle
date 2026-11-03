# Windy City Raffle — self-hosted fundraising website starter

An original, independent platform offering core features comparable to Raffle Creator. It is **not affiliated with or copied from Raffle Creator**.

## Included now

- Branded public fundraiser landing page (responsive on phones and desktops)
- One configurable campaign or multiple campaigns, each with its own shareable URL
- Prize description, **direct photo upload** or HTTPS photo URL, fundraiser description, goal tracker and published rules
- Ticket bundles **1 / 5 / 10 / 25 / 50 / 100**, editable prices
- Buyer name, email, phone and player/seller attribution
- Checkout integration for **Stripe Checkout** (strictly disabled until appropriately approved)
- **No percentage-based fee charged by this website**; approved processor charges its own fees
- Stripe-signed webhooks with amount verification and idempotent entry issuance
- Unique ticket numbers generated only on payment-confirmation webhook
- Optional SMTP confirmation messages; private receipt pages
- Sales dashboard, entrant CSV export, campaign open/close controls
- Multiple drawings with a cryptographically secure RNG, recorded pool hash and winner history
- Refunded entries are voided through Stripe webhook reconciliation
- Fully working demo mode that **simulates** payments, entry issuance and winner drawing

## Quick start — PC

1. Download and unzip the project.
2. Install Python 3.11 or newer from https://www.python.org/downloads/ and select **Add python.exe to PATH**.
3. Double-click `START_DEMO_WINDOWS.bat`.
4. Open http://127.0.0.1:8000/ (if it does not open automatically).
5. Dashboard: http://127.0.0.1:8000/admin
6. Default **DEMO ONLY** password: `change-me-in-production`.
7. Click the test campaign. Choose a package and press **Simulate checkout**. Return to Dashboard, close the raffle and draw a test winner.

Mac/Linux: `python3 -m pip install -r requirements.txt && python3 app.py`.

The SQLite database is created automatically in `data/raffles.db` and stores demo data between runs. Delete that file to reset the demo. **Do not store real personal information in demo mode.**

## How real card payments and payout would work

Once your organization and specific raffle have been cleared by local counsel and your processor gives written approval:

1. Use a processor account **explicitly authorized** to handle that raffle activity. This starter's provider implementation is for Stripe only.
2. The site creates a hosted Stripe Checkout Session for your ticket package.
3. Stripe processes the transaction; your website never stores credit card numbers.
4. A verified Stripe webhook marks the order paid and issues unique numbered tickets.
5. Ticket proceeds go into **your own approved Stripe account**, which pays out to your connected bank account according to its payout settings. This app does not hold or transfer funds.
6. Administrator closes sales and draws one or more winners from eligible paid tickets.

The site charges **no platform commission**. Stripe may charge card-processing fees and may restrict raffles.

**Important:** The current user has indicated the sponsoring organization may be a for-profit Illinois LLC. An Illinois for-profit LLC generally cannot directly obtain a conventional raffle license under 230 ILCS 15/2. A website does not change that. Do not switch on live raffle sales or submit payments under a misleading merchant category or different purpose. For a lawful sweepstakes, separate legal review and changes to entry methods/rules are needed; this code currently implements a conventional ticket raffle, *not* a free-entry sweepstakes.

## Live deployment prerequisites

**Do not set `ENABLE_LIVE_PAYMENTS=1` without documented approval of this precise activity.** The application refuses to start if any required gate is missing.

- A qualified raffle organizer, local licensing and written counsel review of online sales, geography and age restrictions.
- Written approval from the actual payment provider for the specific campaign and entity.
- An HTTPS hosting provider, persistent storage, secure server environment.
- A production password and random session secret, configured through environment variables (do not commit secrets to Git).
- A PCI-compatible hosted processor checkout, webhook endpoint, and optional reliable email provider.
- A pre-launch security/privacy/accessibility review, operational backups, incident response, email deliverability and legal rules.

Environment variable example (set via your hosting dashboard, not public files):

```bash
DEMO_MODE=0
ADMIN_PASSWORD='<at least 20 strong random characters>'
SESSION_SECRET='<random 64+ character value>'
PUBLIC_BASE_URL=https://your-approved-domain.com
ENABLE_LIVE_PAYMENTS=1
RAFFLE_LEGAL_APPROVED=1
PROCESSOR_APPROVED=1
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
DATA_DIR=/persistent-volume/data
```

Stripe webhook endpoint: `https://your-approved-domain.com/webhook/stripe`. Subscribe to `checkout.session.completed`, `checkout.session.async_payment_succeeded`, and `charge.refunded`. Other payment-related event cases and dispute reconciliation should be added for a production launch.

## Production limitations to address before handling $20,000+

This is a **working starter, not a certified production ticketing product**. For actual operations, the following still need to be completed and independently tested:

- Secure hosted infrastructure with a persistent managed database (PostgreSQL recommended); SQLite is for one-server trials and is not ideal for horizontally scaled workloads.
- Production threat model, session & CSRF review, checkout abuse and rate limiting behind a real reverse proxy, audit logging and automated backups.
- Additional payout/reconciliation reports, refunds and disputes handling, chargebacks, email retries, purchaser verification, tax and age controls, and availability monitoring.
- Raffle permit display and online sales compliance reviewed for the operator's jurisdiction.
- A site privacy policy, official campaign rules, customer support contact and accessibility testing.
- A third-party-reviewed drawing protocol if the raffle license requires an independently certified process.

## Developer tests

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

## Design choices

- Dark / white / lime theme, branded for Windy City Baseball. All prize data and tier prices are placeholders and editable; photos can be uploaded after saving a campaign.
- Configuration and local database are intentionally separate from the website source.
- No copies of Raffle Creator's code, trademarks, page markup, artwork or customer data were used.

## Legal primary references

- Illinois Raffles and Poker Runs Act: https://ilga.gov/Legislation/ILCS/Articles?ActID=1400&ChapterID=25
- Stripe restricted businesses: https://stripe.com/legal/restricted-businesses

## Deploy to Render — private test controls / public preview (November 2026)

This version includes `render.yaml` for a **public, read-only demo**, with an admin-only simulated checkout. Visitors **cannot buy tickets or create orders**. **Do not represent the preview as a live raffle.**

### What you need

- Render account and a GitHub repository containing the **contents of this folder** at the repository root (not a zip file).
- A paid Render **web service** for persistent disk support. The included Blueprint uses `0.5c-512mb` compute with a 1 GB persistent disk in Ohio. Review the monthly charge at checkout.
- A randomly generated, strong admin password of **at least 20 characters**. Do not reuse the local demo password or share the admin password in ChatGPT.

### Deploy using the Blueprint

1. Unzip the release, create a private GitHub repository, and upload the source files into the repository root. **Do not upload `data/`, demo databases, `.env`, or local credentials.** The supplied `.gitignore` lists these exclusions.
2. Go to https://dashboard.render.com/blueprints and select **New Blueprint Instance**. Connect GitHub and choose your repository.
3. Render detects `render.yaml`. Review service `windy-city-raffle-preview`, region Ohio, paid compute, and 1 GB persistent disk. The persistent disk is important; otherwise test records and photos disappear during deploys. You can choose a new service name if it is already taken.
4. When prompted for `ADMIN_PASSWORD`, generate a unique 20+ character password. The Blueprint will generate `SESSION_SECRET` automatically. Do not paste those values into your GitHub repository.
5. Deploy. When the status is **Live**, open the `https://...onrender.com` address shown on your service dashboard. The homepage and public raffle page should display **PUBLIC PREVIEW**; public checkout must be disabled.
6. Open `/admin/login` on that address and enter your private admin password. You can edit prizes, bundle prices, drawing dates, photos, and simulate checkout while logged in. The public cannot simulate orders.
7. Check `https://YOUR-RENDER-URL/health`. It should show `"demo":true` and `"live_payments_enabled":false`.
8. Later, add `raffle.gowindycity.com` under Render **Settings → Custom Domains**, then create the DNS record Render specifies at the domain registrar. Don't guess DNS values.

### If creating a service manually instead

Choose **New → Web Service**, GitHub source, environment/runtime **Docker**, 0.5 CPU/512 MB paid plan or better, **Health Check Path** `/health`, and add a **1 GB persistent disk** at `/var/data`. Set these environment variables:

```text
DATA_DIR=/var/data
DEMO_MODE=1
PUBLIC_PREVIEW=1
ENABLE_LIVE_PAYMENTS=0
RAFFLE_LEGAL_APPROVED=0
PROCESSOR_APPROVED=0
ADMIN_PASSWORD=(your secure private 20+ character password)
SESSION_SECRET=(your secure private 32+ character random secret)
```

Render automatically provides `RENDER_EXTERNAL_URL`, which this app uses for its HTTPS public base URL. The `Dockerfile` supplies the start command, so no extra command is necessary.

### Important release constraints

- The site is **not live** until Render reports a successful deploy. A Render account alone does not deploy it; it needs a repository and Render service configuration.
- A public preview is **not** a licensed or payment-enabled raffle. No real payments or payouts happen in this version.
- Even after legal and processor approval, extra security testing and operational improvements are necessary before collecting payment details or handling $20,000+ in real sales. The current production flow is starter code.
- Upgrading a preview to sales is **not** as simple as setting payment variables. Illinois organizer eligibility, local license, approved processor, campaign rules, chargeback handling, and production security should be completed before enabling sales.
