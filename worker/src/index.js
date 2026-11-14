/**
 * Telegram webhook for dismiss buttons.
 *
 * The daily digest runs once and then nothing is listening, so a tap used to sit in
 * Telegram's queue until the next morning: no toast, no visible change, and the button
 * still sitting there inviting a second tap. This Worker is the listener. It answers the
 * callback while it is still valid, takes the button off the keyboard, and parks the
 * token in KV for the daily job to collect.
 *
 * KV is a queue here, not the record. The daily job merges tokens into state.json and
 * then acks them, so restoring a deadline by editing state.json stays restored.
 */

const DONE_PREFIX = "done:";
const KEEP_PENDING_FOR = 60 * 60 * 24 * 60; // 60 days, far past any digest that could still be acked

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const route = `${request.method} ${url.pathname}`;

    switch (route) {
      case "POST /telegram":
        return handleTelegram(request, env);
      case "GET /dismissals":
        return listDismissals(request, env);
      case "POST /dismissals/ack":
        return ackDismissals(request, env);
      default:
        return new Response("Not found", { status: 404 });
    }
  },
};

async function handleTelegram(request, env) {
  // Telegram echoes this header back on every delivery. Without it the endpoint is a
  // public URL that anyone could use to forge dismissals.
  if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET) {
    return new Response("Forbidden", { status: 403 });
  }

  const update = await request.json().catch(() => null);
  const query = update?.callback_query;
  // Always 200 on anything we do not handle: a non-2xx makes Telegram redeliver the
  // same update, and it will keep doing so for an update we are never going to want.
  if (!query || !String(query.data || "").startsWith(DONE_PREFIX)) {
    return jsonResponse({ ok: true });
  }

  const chatId = String(query.message?.chat?.id ?? "");
  if (chatId !== String(env.TELEGRAM_CHAT_ID)) {
    return jsonResponse({ ok: true });
  }

  const token = String(query.data).slice(DONE_PREFIX.length);
  const label = buttonLabel(query);

  await env.DISMISSALS.put(
    token,
    JSON.stringify({ title: label, at: new Date().toISOString() }),
    { expirationTtl: KEEP_PENDING_FOR },
  );

  // The toast is the whole point of answering here rather than tomorrow: the callback id
  // expires within about a minute, which is why the old design could never show one.
  await callTelegram(env, "answerCallbackQuery", {
    callback_query_id: query.id,
    text: `Dismissed: ${label}`,
  });

  await removeButton(env, query);
  return jsonResponse({ ok: true });
}

/** Drop just the tapped button, leaving the rest of the digest's keyboard intact. */
async function removeButton(env, query) {
  const keyboard = query.message?.reply_markup?.inline_keyboard ?? [];
  const remaining = keyboard
    .map((row) => row.filter((button) => button.callback_data !== query.data))
    .filter((row) => row.length > 0);

  await callTelegram(env, "editMessageReplyMarkup", {
    chat_id: query.message.chat.id,
    message_id: query.message.message_id,
    reply_markup: { inline_keyboard: remaining },
  });
}

function buttonLabel(query) {
  const keyboard = query.message?.reply_markup?.inline_keyboard ?? [];
  for (const row of keyboard) {
    for (const button of row) {
      if (button.callback_data === query.data) {
        return String(button.text || "").replace(/^✅\s*/, "") || "deadline";
      }
    }
  }
  return "deadline";
}

async function listDismissals(request, env) {
  if (!authorized(request, env)) {
    return new Response("Unauthorized", { status: 401 });
  }

  const listed = await env.DISMISSALS.list();
  const dismissals = await Promise.all(
    listed.keys.map(async ({ name }) => {
      const stored = await env.DISMISSALS.get(name, { type: "json" });
      return { token: name, title: stored?.title ?? "", at: stored?.at ?? "" };
    }),
  );
  return jsonResponse({ dismissals });
}

async function ackDismissals(request, env) {
  if (!authorized(request, env)) {
    return new Response("Unauthorized", { status: 401 });
  }

  const body = await request.json().catch(() => null);
  const tokens = Array.isArray(body?.tokens) ? body.tokens : [];
  await Promise.all(tokens.map((token) => env.DISMISSALS.delete(String(token))));
  return jsonResponse({ ok: true, acked: tokens.length });
}

function authorized(request, env) {
  return request.headers.get("Authorization") === `Bearer ${env.DISMISS_API_SECRET}`;
}

async function callTelegram(env, method, payload) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    // Logged rather than thrown: the dismissal is already in KV, and failing the request
    // would only make Telegram redeliver a tap we have already recorded.
    console.log(`${method} failed: ${response.status} ${await response.text()}`);
  }
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
