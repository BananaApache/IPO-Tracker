// Typed client for the FastAPI backend.
//
// These interfaces are hand-mirrored from the Pydantic models in
// backend/api/issuers.py. That duplication is deliberate for now -- there are
// two models -- but it is a real drift risk: nothing fails if the backend
// renames a field, the value just arrives as undefined. Phase 6 should generate
// this file from /openapi.json instead of maintaining it by hand.

export type IssuerStatus = "filed" | "priced" | "listed" | "withdrawn";

export interface Issuer {
  id: number;
  cik: string;
  legal_name: string;
  ticker: string | null;
  exchange: string | null;
  sector: string | null;
  status: IssuerStatus;
  first_filed_at: string | null;
}

export interface Page<T> {
  data: T[];
  meta: { next_cursor: string | null };
}

// No NEXT_PUBLIC_ prefix, on purpose. Next.js only inlines NEXT_PUBLIC_* vars
// into the browser bundle, so this value stays on the server -- which is what
// lets the API live on a private network in production while the browser only
// ever talks to Next.
const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

export async function listIssuers(
  params: { status?: IssuerStatus; limit?: number; cursor?: string } = {},
): Promise<Page<Issuer>> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);

  const response = await fetch(`${API_BASE_URL}/api/v1/issuers?${query}`, {
    // Next 16 does not cache fetch by default, but state it anyway: this is a
    // surveillance feed, and a stale filing list is worse than a slow one.
    cache: "no-store",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    // Thrown on the server, caught by app/error.tsx. The message is safe to
    // surface because it carries no upstream response body.
    throw new Error(`Issuer API returned ${response.status} ${response.statusText}`);
  }

  return response.json();
}
