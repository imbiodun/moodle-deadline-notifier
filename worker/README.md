# Dismissal Worker

Answers Telegram dismiss taps as they happen. Without it, a tap sits in Telegram's queue
until the next daily run: no toast, no visible change, and the button still tappable.

The Worker is the listener. On a tap it answers the callback (toast), removes that button
from the keyboard, and parks the token in KV. The daily job collects pending tokens,
merges them into `state.json`, then acks them so KV drains.

KV is a queue, not the record. `state.json` stays authoritative, so restoring a deadline
by editing it stays restored.

## Deploy

Everything below runs from this `worker/` directory.

```bash
npm install -g wrangler
wrangler login
```

**1. Create the KV namespace** and paste the returned id into `wrangler.toml`:

```bash
wrangler kv namespace create DISMISSALS
```

**2. Set your chat id** in `wrangler.toml` under `[vars]` — the same value as the
`TELEGRAM_CHAT_ID` GitHub secret.

**3. Generate two secrets** and keep them to hand:

```bash
openssl rand -hex 32   # webhook secret, shared with Telegram
openssl rand -hex 32   # API secret, shared with GitHub Actions
```

**4. Deploy**, and note the `https://deadline-dismissals.<subdomain>.workers.dev` URL:

```bash
wrangler deploy
```

**5. Store the Worker's secrets** (each prompts for the value). Secrets attach to a
Worker that already exists, which is why this follows the deploy rather than preceding
it; they take effect immediately, with no redeploy:

```bash
wrangler secret put TELEGRAM_BOT_TOKEN       # same token as the GitHub secret
wrangler secret put TELEGRAM_WEBHOOK_SECRET  # first generated secret
wrangler secret put DISMISS_API_SECRET       # second generated secret
```

**6. Tell GitHub Actions about it — do this _before_ step 7.** Setting a webhook disables
`getUpdates`, so a run between the two steps would fail on a 409.

```bash
gh secret set DISMISS_API_URL    --body 'https://deadline-dismissals.<subdomain>.workers.dev'
gh secret set DISMISS_API_SECRET --body '<second generated secret>'
```

**7. Point Telegram at the Worker:**

```bash
curl -s "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -d url='https://deadline-dismissals.<subdomain>.workers.dev/telegram' \
  -d secret_token='<first generated secret>' \
  -d allowed_updates='["callback_query"]'
```

Expect `{"ok":true,"result":true,"description":"Webhook was set"}`.

## Verify

```bash
curl -s "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

`pending_update_count` should be 0 and `last_error_message` absent. Then tap a ✅ button
on a digest: the toast should appear and the button should vanish within a second. The
token is now queued — check it:

```bash
curl -s https://deadline-dismissals.<subdomain>.workers.dev/dismissals \
  -H 'Authorization: Bearer <second generated secret>'
```

`wrangler tail` streams live logs if a tap does not land.

## Reverting

```bash
curl -s "https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook"
gh secret delete DISMISS_API_URL
gh secret delete DISMISS_API_SECRET
```

The daily job falls back to `getUpdates` polling whenever those two secrets are absent.
