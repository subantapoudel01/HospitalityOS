# Deployment

Getting HospitalityOS off `localhost` and onto a server real hotel staff
can reach.

Everything in **Part 1** needs your accounts and your payment card, so you
have to do it. Everything from **Part 2** onward is scripted.

---

## What you are deploying

One VPS running five containers behind Traefik, all on one hostname:

```
                    ┌─────────────────────────────┐
   :443  ────────►  │ traefik    TLS, Let's Encrypt│
                    └──────┬───────────────┬──────┘
                     /api  │               │  everything else
                    ┌──────▼──────┐  ┌─────▼──────┐
                    │  backend    │  │  frontend  │
                    │  FastAPI    │  │  Next.js   │
                    └──┬───────┬──┘  └────────────┘
                       │       │
              ┌────────▼─┐  ┌──▼──────┐
              │ postgres │  │  redis  │     no published ports
              │ pgvector │  └─────────┘
              └──────────┘
```

**One hostname, not two.** Traefik routes `/api` to the backend and
everything else to the frontend, so the staff session cookie is
same-origin. Splitting the API onto `api.yourhotel.com` would need
`SameSite=None` and credentialed CORS, which is strictly weaker.

---

## Part 1 — What you must do yourself

I cannot create accounts, enter payment details, or buy domains. These
five steps are yours.

### 1. Pick a provider and create the server

Any of these is fine. The app is not demanding; the embedding model is the
memory floor.

| Provider | Suggested size | Roughly |
|---|---|---|
| **Hetzner CX22** (cheapest) | 2 vCPU, 4 GB, 40 GB | €4/mo |
| **DigitalOcean** | 2 vCPU, 4 GB | $24/mo |
| **AWS Lightsail** | 2 vCPU, 4 GB | $24/mo |

**4 GB RAM, not 2.** The baked-in embedding model plus Postgres plus two
uvicorn workers will OOM on a 2 GB box under any real load.

Choose **Ubuntu 24.04 LTS**, add your SSH public key during creation, and
pick a region near your guests — Hetzner and DigitalOcean both have
Singapore, which is the closest low-latency option to Nepal.

### 2. Point a domain at it

Buy a domain (Namecheap, Cloudflare, Porkbun) and add one record:

| Type | Name | Value |
|---|---|---|
| A | `@` or `reception` | your server's IPv4 |

**Do this before deploying.** Let's Encrypt verifies over HTTP; if DNS
does not resolve yet, certificate issuance fails and Traefik serves a
browser warning. Check it has propagated:

```bash
dig +short reception.yourhotel.com
```

If you use Cloudflare, set the record to **DNS only** (grey cloud) for the
first deploy. Proxied records intercept the ACME challenge.

### 3. Meta / WhatsApp Business verification

Independent of the server and the long pole — **1 to 3 weeks**. Start it
now, in parallel. See [Slice F, below](#slice-f--whatsapp).

---

## Part 2 — Prepare the server

```bash
ssh root@YOUR_SERVER_IP 'bash -s' < infra/scripts/bootstrap-server.sh
```

Installs Docker, creates a `hospitality` user, enables `ufw` (22/80/443
only) and unattended security updates.

> **Docker bypasses ufw.** Publishing a port in compose writes iptables
> rules that ufw does not see, so a container with `-p 5432:5432` is on the
> public internet regardless of the firewall. `docker-compose.prod.yml`
> publishes only 80 and 443 — check any change to it against this.

---

## Part 3 — Deploy

```bash
ssh hospitality@YOUR_SERVER_IP
```

```bash
git clone https://github.com/subantapoudel01/HospitalityOS.git /opt/hospitalityos && cd /opt/hospitalityos
```

```bash
cp .env.prod.example .env.prod && nano .env.prod
```

Fill in every blank. Generate the secrets rather than inventing them:

```bash
openssl rand -base64 32
```

```bash
docker compose -f infra/docker/docker-compose.prod.yml run --rm backend python -m app.scripts.seed_admin --print-secret
```

Leave **`STAFF_API_TOKEN` empty**. It grants cross-tenant access with no
identity and no audit trail; real accounts replaced it.

Then:

```bash
./infra/scripts/deploy.sh
```

It builds, migrates in a one-off container, starts the stack, and waits
for `/health`. Migrations run **before** the new containers start and a
failure aborts with the old ones still serving.

### Create the first login

```bash
docker compose -f infra/docker/docker-compose.prod.yml --env-file .env.prod run --rm backend python -m app.scripts.seed_admin --email admin@yourhotel.com --hotel-id 1
```

The password is generated and **printed once**. Copy it immediately.

Then sign in at `https://reception.yourhotel.com/staff/login`.

---

## Part 4 — Before staff touch it

- [ ] `https://…/staff` redirects to the login page in a private window
- [ ] `https://…/widget` loads and answers a question
- [ ] The padlock is green and `http://` redirects to `https://`
- [ ] `STAFF_API_TOKEN` is empty in `.env.prod`
- [ ] `.env.prod` is not in git — `git status` shows nothing
- [ ] A backup has run **and been restored once** into a scratch database
- [ ] Each staff member has their own account, not a shared one

### Backups

```bash
crontab -e
```

```
0 3 * * * cd /opt/hospitalityos && ./infra/scripts/backup-db.sh >> /var/log/hos-backup.log 2>&1
```

A dump on the same VPS as the database is not a backup — it dies with the
server. Copy them off the box. And note what is in them: **complete guest
conversation transcripts**, which is personal data.

---

## Operating it

| Task | Command |
|---|---|
| Deploy a new version | `git pull && ./infra/scripts/deploy.sh` |
| Logs | `docker compose -f infra/docker/docker-compose.prod.yml logs -f backend` |
| Restart one service | `docker compose -f … restart backend` |
| Database shell | `docker compose -f … exec postgres psql -U hospitalityos` |
| Add a staff account | `… run --rm backend python -m app.scripts.seed_admin --email x@y.com --hotel-id 1` |
| Reset a password | same, plus `--reset-password` |
| Revoke everyone | change `JWT_SECRET`, then `docker compose … up -d backend` |

### Rolling back

```bash
git log --oneline -5
```

```bash
git checkout <previous-sha> && ./infra/scripts/deploy.sh
```

Note that this does **not** roll back migrations. If the bad deploy
included one, `alembic downgrade -1` first — and confirm it is reversible,
which CI checks on every push.

---

## Honest limits of this setup

Worth knowing before staff rely on it:

- **One server, no redundancy.** If it reboots, the site is down until it
  comes back. Fine for a pilot at one property; not an SLA.
- **Sessions are stateless JWTs.** Signing out clears the cookie, but a
  token already copied elsewhere stays valid until it expires (12h).
  Deactivate the user, or rotate `JWT_SECRET` to cut every session at once.
- **The login throttle is per-process.** With `--workers 2` the real
  ceiling is 20 attempts per 15 minutes, not 10. Move it to Redis before
  raising the worker count.
- **The session cookie is readable by JavaScript.** An XSS in the
  dashboard can steal a session. Now that the API is same-origin here,
  switching to `httpOnly` is a small change and worth making — see the
  note in `backend/app/platform/api/auth_routes.py`.
- **No log aggregation or alerting.** You find out it is down by looking.

---

## Slice F — WhatsApp

Not blocked on the server, but on Meta, so **start it while you deploy**.

1. **Meta Business Account** — <https://business.facebook.com>
2. **Business verification** — this is the 1–3 week wait. You will need:
   - Business registration document (company registration / PAN / VAT)
   - Proof of address — a utility bill in the business's name
   - A business phone number Meta can call
   - A website whose domain matches the business
3. **WhatsApp Business Platform** app in the Meta developer console
4. **A dedicated phone number** — one *not* already registered to the
   WhatsApp consumer app. A staff member's personal number will not work
   and un-registering it later is painful.
5. **Message templates**, submitted for approval separately (~1–2 days)

Once approved, the webhook lands at `https://…/api/receptionist/whatsapp`
and `WHATSAPP_BUSINESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` go in
`.env.prod`. Those placeholders already exist in `.env.example`; the
handler does not yet.

**The website requirement in step 2 is why deploying first helps** —
`https://reception.yourhotel.com` is a verifiable business presence.
