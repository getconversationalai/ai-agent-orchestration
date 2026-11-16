---
name: security
description: The security rules that prevent real breaches — auth, multi-tenant isolation, input handling, AI/LLM surfaces, webhooks, secrets, database/RLS, outbound fetches, money. Read only the section for what you're touching.
sections:
  - Two rules that apply everywhere
  - Auth & sessions
  - Authorization
  - Multi-tenant isolation
  - Input & output
  - AI / LLM features
  - Browser (CSRF, CORS, headers)
  - Webhooks
  - Secrets
  - Database & row-level security
  - Outbound requests (SSRF)
  - Money & race conditions
  - Errors & logging
  - Before merging
---
# Security essentials

Read just the section for what you're touching. Scale effort to the risk: the basics below apply at every stage, including prototypes; heavier hardening (pen tests, compliance work, full test matrices) comes as the project matures and handles real user data.

## Two rules that apply everywhere
- **Fail closed.** On an error, missing config or anything ambiguous, deny. Never let an exception fall through to "allow".
- **Verify before trusting.** Every id, token, signature or ownership claim from outside the server is checked before it's used.

## Auth & sessions
- Verify the session/JWT on the server on every request, through one shared middleware. Missing or invalid → 401.
- Take `userId` / `tenantId` from the verified token, never from the request body, query or URL.
- Hash passwords with bcrypt/scrypt/argon2. Store API keys and refresh tokens only as hashes; compare in constant time; never log them.
- Generate tokens (sessions, resets, API keys) with a cryptographically secure random generator, never `Math.random`. Reset tokens are single-use and short-lived.
- Issue a new session id at login. Make logout and revocation actually stop a token (short-lived access tokens or a server-side check).
- OAuth: validate `state`, allowlist `redirect_uri` exactly, use PKCE, and verify the ID token's signature, issuer, audience and expiry.
- Require MFA for admin roles, checked on the server.

## Authorization
- Check that *this* user may do *this* action on *this* record — logging in isn't permission.
- Roles and permissions come from server data, never the request. Unknown action → denied.
- Admin and cross-tenant actions need an explicit check and get audit-logged.

## Multi-tenant isolation
The most damaging bug class in SaaS. Treat it as load-bearing.
- Every query on a tenant table filters by the tenant id from the verified session, including lookups by primary key, updates, deletes and bulk operations.
- Any id the client sends must be confirmed to belong to the caller's tenant before use.
- Tables without their own tenant column are scoped through their parent.
- Don't rely on one hand-written filter alone: add row-level security, a scoped query helper, or a lint rule as a second layer.
- Test it: Tenant A's token against Tenant B's ids must return 403/404, never data.

## Input & output
- Validate every request against an explicit schema (zod, pydantic). Reject bad input with a 4xx; don't silently coerce.
- Parameterized queries only — never build SQL or shell commands by string concatenation.
- Escape output for where it goes (HTML, URL, shell). Use framework escaping and a Content-Security-Policy; sanitize rich HTML with an allowlist.
- Cap body size, string/array lengths, file sizes and page sizes.

## AI / LLM features
When an app sends user-reachable content to a model, or lets a model take actions:
- Treat user messages, tool results, retrieved documents and web pages as **data, never instructions**. Keep them out of the system prompt; put them in a clearly delimited user block the content can't break out of.
- Strip your own control markers and hidden text (`display:none`, white-on-white) from untrusted input.
- Authorize every tool call as the end user/tenant, not with the agent's own privileges. Treat unknown tools as write-capable.
- Write or irreversible actions triggered by the model need human approval by default.
- Never trust amounts, ids, prices or recipients the model outputs — re-check them against server data before acting.
- Only this tenant's data goes into the prompt. Screen output for leaked secrets, system-prompt text and other tenants' data, and make sure that screening is switched on in production.
- Never render model output as raw HTML.
- Model-initiated URL fetches follow the SSRF rules below. Rate-limit and cost-cap model calls per tenant.

## Browser (CSRF, CORS, headers)
- Cookie-based sessions: state-changing requests need CSRF protection (SameSite cookies plus a token or a strict Origin check).
- CORS: allowlist origins. Never echo back the request's Origin together with `Allow-Credentials: true`.
- Send HSTS, `X-Content-Type-Options: nosniff`, `frame-ancestors 'none'` and a CSP. Session cookies are `Secure`, `HttpOnly`, `SameSite`.
- Rate-limit login, signup, password reset and other abuse-prone or expensive endpoints.

## Webhooks
- Verify the sender's signature (HMAC over the raw body, constant-time compare) before touching the database or doing anything else.
- Identify the tenant from the verified payload, never from an unverified field in it.
- Reject old timestamps and dedupe event ids (replay protection).
- No secret configured or verification fails → reject.

## Secrets
- Never commit secrets: `.env*` is gitignored and only `.example` files are committed.
- Service-role/admin keys stay on the server, never in the client bundle.
- Encrypt stored third-party credentials (AES-256-GCM or a managed vault) and store a key version so keys can be rotated.
- Fail at startup if a required secret is missing.

## Database & row-level security
- Enable RLS on tenant tables. UPDATE policies need both `USING` and `WITH CHECK`; INSERT needs `WITH CHECK`.
- `SECURITY DEFINER` functions: pin `search_path`, check ownership inside the function, and revoke execute from PUBLIC (not just anon/authenticated) if they're server-only.
- Sensitive tables: RLS on, no policies, grants revoked — service role only.
- Storage bucket policies name an explicit role and restrict writes to the owner's folder.
- After locking something down, query the live grants to confirm it worked.

## Outbound requests (SSRF)
- When the server fetches a URL a user (or the model) supplied, block private, loopback, link-local and cloud-metadata addresses, including IPv6-mapped forms.
- Re-check every redirect and pin the resolved IP. Don't forward auth headers to a different origin.

## Money & race conditions
- Payments, credits, coupon redemptions and "send once" actions need an idempotency key or unique constraint so a retry applies exactly once.
- Balance/quota/inventory updates use atomic database operations, row locks or constraints — never read-then-write across a network hop.

## Errors & logging
- Handle the error from every query you branch on: `const { data } = await q; if (!data) return;` silently swallows permission errors. Check `error` first.
- Error responses never include stack traces, SQL, secrets or internal hostnames.
- Audit-log logins, permission changes, admin actions and data exports (who, what, when, from where). Ordinary users can't read the audit log.
- Never put tokens, session ids or personal data in URLs or logs.

## Before merging
For changes touching auth, tenant data, webhooks, RLS, secrets, money or AI tool wiring, check:
- [ ] Every tenant query is scoped by the server-derived tenant id, and client-sent ids are re-verified.
- [ ] New input is schema-validated; errors are handled, not swallowed; failures deny.
- [ ] Webhooks verify signatures first; money paths are idempotent and race-safe.
- [ ] No secrets committed or logged; new keys are server-only.
- [ ] AI: untrusted content stays data, tool calls run at the user's privilege, writes need approval.
- [ ] There's a test for the main way this could be abused (at minimum, a cross-tenant denial test).
