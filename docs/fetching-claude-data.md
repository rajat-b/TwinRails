# Fetching Claude's usage data

Why [`claude_client.py`](../src/core/claude_client.py) has separate OAuth and
cookie transports, and why the cookie transport cannot use plain Python HTTP.

---

## The constraint

`claude.ai` sits behind Cloudflare, which fingerprints the **TLS handshake**
(JA3) to spot non-browser clients. Python's `urllib`/`requests` use OpenSSL,
whose handshake does not look like Chrome's, and the request is challenged
before any header is read.

Verified directly on **2026-09-20**, a plain unauthenticated GET to
`https://claude.ai/api/organizations`:

```
HTTP/1.1 403 Forbidden
Cf-Mitigated: challenge
Server: cloudflare
<title>Just a moment...</title>
```

This is why the client uses `curl_cffi` with `impersonate="chrome124"`: it
forges a real Chrome TLS fingerprint, and it is the **only** reason the
endpoint answers at all.

> **Do not "simplify" this to `requests`.** It will not fail loudly at import
> time — it will fail at every fetch, and look like an expired session key.

## Headers cannot fix it

This was worth checking, because the fix would be trivial if it worked. It
does not.

A competing project ([`niccolo-sabato/claude-usage-widget`](https://github.com/niccolo-sabato/claude-usage-widget))
hit the same wall and tried exactly that route — a browser `User-Agent`, an
`anthropic-client-platform: web_claude_ai` header, the lot. Their transport
code records the outcome:

> Python's urllib uses OpenSSL and gets a 403 challenge **regardless of how
> browser-shaped the headers are**.

They kept a subprocess `curl` call instead (on Windows, curl uses schannel —
the same TLS stack Edge and Chrome use — so its JA3 matches a real browser).
They also keep a `scripts/compare-auth.py` diagnostic specifically to tell
"session key expired" apart from "the plain-Python transport regressed".

Two independent implementations reached the same conclusion. The fingerprint
is the gate, not the headers.

## The second route: OAuth, on a different host

Claude Code stores an OAuth access token on disk. There is a usage endpoint
that accepts it, and it is **not on `claude.ai`**:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken>
anthropic-beta: oauth-2025-04-20
```

Token source: `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`.
The same file carries `subscriptionType` (the plan name). Claude Code's
`CLAUDE_CONFIG_DIR` override is supported too. The widget reads this file on
each fetch and never copies the token into its own config.

Only the Claude Code **CLI** writes that file. Checked on 2026-09-20: the
desktop app alone did not create it, so someone who only uses the desktop app
lands on the cookie route until they sign in to the CLI once.

This host answers plain `requests` with HTTP 200 — no browser challenge — so
the OAuth route does not use `curl_cffi`. That stays for the cookie route only.

### What the widget reads

Only these parts of the usage response are used; the rest is ignored.

| Field | Used for |
|---|---|
| `five_hour`, `seven_day` | The session and weekly windows (`utilization`, `resets_at`) |
| `limits` | One entry per reported limit, including per-model weekly limits (`scope.model.display_name`) |
| `extra_usage`, `spend` | The usage-credits card, only when both a cap and an amount used are present |
| any object with `limit_dollars` + `used_dollars` | A dollar grant card, e.g. **Cloud Session Credits** (see below) |
| `seven_day_breakdown.rows` | The flyout's "This week by product" stacked bar (`key`, `display_name`, `percent`) |

Any other top-level object shaped like a window (`utilization` plus
`resets_at`) is picked up too, so a new or renamed window still appears
without a code change. Objects carrying `limit_dollars` are excluded from
that: they are money, not windows.

**Cloud session credits.** Found 2026-09-25 by comparing a live cookie-route
response with claude.ai's own usage page. The page's "Cloud session credits ·
Included credit · $215 of $250 left · Expires November 5" is a top-level
object under an internal code name, with `limit_dollars` 250,
`used_dollars` 34.71, `remaining_dollars` 215.29 and `resets_at` on the
expiry date. The widget finds such grants by those dollar fields rather than
by the name, because the name can change. A known name only picks the label;
an unknown one still shows, as "Included Credit". `resets_at` becomes an
expiry date on the card, not a window to pace against. This was seen on the
cookie route only: the OAuth response lists the same top-level keys, but this
one was null there on 2026-09-20, before the grant existed, so OAuth delivery
is unverified.

**The credit card is not paced, on purpose** (decided 2026-09-25). It shows
money left and the expiry date. There is no time rail and no projection, for
two reasons:

- The response gives the grant's expiry but not its start, so there is no
  honest "how far through" to draw.
- The rails' warning points the wrong way here. Running out early is not a
  lockout: cloud sessions fall back to the plan's normal usage. The real risk
  is money left unused at expiry.

It is also a one-time launch promotion: $250 on Max and $100 on Pro,
claimable until 7 October, expiring on 5 November for everyone. So the card
goes away on its own.

Rejected:

- **A user-entered start date driving the rails.** It would need a Settings
  field, reversed warning colours ("behind" is the bad case), and handling for
  renewals, all for a six-week promotion.
- **A "$ per day to use it all" line.** It assumes the plan stays active until
  the expiry date, which is not true for everyone. Cloud sessions are a
  Pro/Max feature, and Anthropic's [credit
  terms](https://www.anthropic.com/legal/credit-terms) cover account closure
  but not a lapsed plan, so the credit may stop being usable earlier.

**Not in this response,** checked on the same date: the usage-credits
*balance* (`spend.balance` is null), the monthly spend cap the page shows
("$5.88 of $5" there, while `spend.used` reported $0.00), and the page's
"Reset for free" offer. The widget does not make another request to get them.

**The breakdown percents are shares of this week's usage** and sum to about
100. They are not fractions of the limit: with the weekly limit at 17%, Claude
Code was reported at 100. That is why the flyout draws them as one 100%
stacked bar. The colours and why they were chosen are in `BREAKDOWN_COLORS`
in `src/ui/theme.py`.

On the account this was verified against, `spend.used` was populated but
`spend.limit` and `extra_usage.used_credits` were null. Money spent is not a
balance, so the shared `_extract_credits` helper returns `None` and the OAuth
view hides the credits card. It does **not** make a cookie request just for
credits. Whether accounts with extra usage enabled get a populated balance is
unverified.

### What the widget shows from this response

- Each scoped model limit in `limits` can be chosen separately for the taskbar.
  All reported model limits also appear automatically in the flyout, even
  before that choice is made. The metric key includes the scope, because
  multiple models can share `weekly_scoped` as their kind. A saved selection
  using the older shared `limits_weekly_scoped` key resolves to the first
  reported scoped model until Settings saves its specific key.
- When both a spending cap and amount used are supplied, the credits card
  shows the amount remaining (`cap − used`). It appears automatically in the
  flyout and can be selected for the taskbar. It uses Claude's reported
  currency. A used amount by itself is not a balance, so a response with only
  that cannot show money left.
- The flyout displays `extra_usage.is_enabled`, an explicit
  `spend_limit_reached` flag, and the labeled `seven_day_breakdown` rows when
  present. Breakdown rows are reported usage figures, not extra quota windows.
  It scrolls when the account has more rows than fit on the screen.

The existing Settings format records where a selected metric appears, but
cannot distinguish a model or credits card explicitly hidden by the user from
one that has never been selected. These Claude cards therefore remain visible
in the flyout when fetched; their checkboxes promote them to the taskbar.
Claude's usage percentages do not provide an exact token allowance or tokens
left, so the widget does not turn them into a token count.

### Why it is the better route

| | Cookie → `claude.ai` | OAuth → `api.anthropic.com` |
|---|---|---|
| TLS fingerprint forgery | Required | Not required (different host, no browser challenge) |
| Setup | Paste a full login credential from devtools | Zero config if Claude Code is signed in |
| Expiry | Re-paste when the cookie expires | Claude Code can refresh it when run; the widget asks the user to run Claude Code after a 401/403 |
| Credential at rest | This widget stores it (DPAPI) | Read from where Claude Code already put it |

The setup difference is not cosmetic. Asking a stranger to paste a full login
credential into a third-party binary is the single largest piece of friction
in the install, and the largest thing to be uneasy about.

## Current dispatch

Both transports are supported, OAuth first:

1. If a Claude Code token is findable, use it. No prompt, no paste.
2. Otherwise fall back to the session cookie, for web-only subscribers who
   never touch Claude Code.

The Settings choice can force either mode. `curl_cffi` stays in the
dependency list because the cookie route still needs it.

In automatic mode the cookie takes over when the server refuses or throttles
the token, never on a network error. A flaky connection must not silently
switch routes and make the failure unreadable. Specifically:

- **No token, or an expired one.** The token Claude Code leaves on disk lasts
  about 8 hours, and only the `claude` CLI refreshes it; seen 2026-09-25,
  `expiresAt` was exactly 8h after the file was written. An expired token is
  treated as no token. Sending it anyway is worse than useless: other tools
  report the endpoint answering it with 429, which looks like rate limiting.
- **401/403.** The token was refused.
- **429, rate limited.** See below.

Each fallback adds a line to the flyout's Claude details saying the cookie is
in use.

**The OAuth usage endpoint rate-limits hard.** On 2026-09-25, polled every
60 seconds, it began answering every request with 429 and `Retry-After: 0`.
That matches Anthropic's own issue tracker (anthropics/claude-code #30930,
#31021, #31637), where tools polling every 30–60s get stuck in a permanent 429
loop. The old handling, a fixed 60-second cooldown, reproduced exactly that
loop. Now:

- A positive `Retry-After` is honoured, within 1–60 minutes.
- With none, the wait doubles 5 → 10 → 20 → 40 → 60 minutes, and resets on
  the next success.
- With no cookie to fall back on (OAuth-only mode, or no key saved), the last
  good reading stays on screen for up to 30 minutes, with its age in the
  error line. It still counts as disconnected, so the taskbar tints Claude's
  rows amber, the existing "not live" cue. After 30 minutes only the error
  remains.

**Sign-in checks are spaced at least 5 minutes apart, whatever the refresh
interval.** Between checks the last sign-in reading is reused, and once it is
a minute old the flyout's Claude details give its age. The refresh interval
still drives the cookie route and Gemini. So a user who wants Claude numbers
every minute picks "Session cookie only", and nobody has to know the endpoint's
limits to avoid tripping them.

Rejected: telling users to set a 5-minute refresh. It would also slow the
cookie route and Gemini for no reason, and people who never read the advice
would still hit the 429 loop. Settings instead explains each login choice as
it is picked. Sign-in only gets an amber note about the 8-hour expiry and the
5-minute updates.

## The terms angle

Which route you use changes where you stand under Anthropic's terms, and not
by a small amount. The cookie route hands this widget a full login credential
and gets past `claude.ai`'s bot check with a forged browser fingerprint; the
Consumer Terms prohibit both automated access and bypassing protective
measures. The OAuth route needs neither, which is why it is the default. Both
still read an undocumented endpoint on a timer. See
[Risks and limits](../README.md#risks-and-limits) in the README.
