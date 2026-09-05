# Where Next.js error boundaries actually catch, and where they don't

**Summary:** `app/error.tsx` cannot render an error that happens on a hard
navigation. It is a Client Component, so it only exists once React has hydrated.
When a Server Component throws during the initial request, nothing has committed
and there is no hydrated tree to render the boundary into — the visitor gets a
blank 500 document. Expected failures must therefore be caught in the Server
Component itself.

This bit us on the one failure that matters most for a dashboard: the backend
being down.

---

## The mental model

Two different things are called "the error boundary":

- **`app/error.tsx`** — a React error boundary. React error boundaries are class
  components with `componentDidCatch`, and this one also needs an `onClick`
  retry. State and event handlers only exist in the browser, so Next requires
  `"use client"`. It is *shipped JavaScript that runs after hydration.*
- **Next's built-in 500 response** — a static document the server returns when
  rendering fails before anything can be streamed.

The first can only help once the second has been avoided.

## What we measured

Tested against a **production build** (`next build && next start`), not `next
dev`, because the dev overlay masks the real behaviour. Two failure kinds × two
navigation kinds.

A soft navigation was exercised by requesting the RSC flight payload the way a
`<Link>` does (`RSC: 1`; Next redirects it to `/?_rsc=…`, so follow redirects).

| | Hard navigation (first load) | Soft navigation (`<Link>`) |
|---|---|---|
| **API down**, caught in the Server Component | `200` — page header plus "Could not load issuers". No JS required. | `200` — flight payload carries the same fallback markup. |
| **Unexpected throw**, uncaught | `500` — document contains only `<title>IPO Surveillance</title>`. A visitor sees a blank page. `error.tsx` never renders. | `200` — flight payload carries `{"digest":"578403141"}` and nothing else; React uses it to render `error.tsx` client-side. |

The bottom-left cell is the gap. The bottom-right cell is what `error.tsx` is
genuinely for.

Note the digest: the thrown message (`PROBE_UNEXPECTED_THROW`) does **not**
appear in the response. Next replaces server error messages with an opaque
digest so internals cannot leak to the client. Good default, but it also means
a hard-navigation failure gives the visitor nothing actionable.

## What we changed

`app/page.tsx` catches its own fetch failure and renders a real fallback:

```tsx
let issuers: Issuer[] = [];
let loadError: string | null = null;

try {
  ({ data: issuers } = await listIssuers({ limit: 25 }));
} catch (cause) {
  loadError = cause instanceof Error ? cause.message : "Unknown error";
}
```

This works on both navigation kinds and with JavaScript disabled, because the
fallback is server-rendered like any other markup.

`app/error.tsx` stays. It is not redundant — it is the net for genuinely
unexpected errors (a render bug, a bad `.map`) on client-side navigations, which
is a real class of failure and the one it can actually catch.

## The principle

**An unreachable backend is an expected operational state for a dashboard, not
an exceptional one.** Expected failures belong in the happy path's control flow,
where they can be rendered deliberately. `error.tsx` is for the unexpected.

Reaching for the error boundary first is the natural instinct — it is literally
named for this — and it is wrong in a way that only shows up in production, on a
visitor's first load, which is the worst place to discover it.

## `global-error.tsx` does not fix this

It replaces the root layout when the layout itself throws, but it is still a
Client Component with the same hydration requirement. Same gap.

## How to re-test

```bash
cd frontend && npm run build && npx next start -p 3002
docker compose stop api

curl -s -o /dev/null -w "%{http_code}\n" localhost:3002/      # hard  -> 200
curl -sL -H "RSC: 1" localhost:3002/ | grep -o "Could not load issuers"   # soft -> present
```

For the uncaught-throw rows, add a temporary route that throws. It needs
`export const dynamic = "force-dynamic"`, or the build fails trying to
prerender it.
