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

// ---------------------------------------------------------------------------
// Listing events and pipeline stats

export type Cohort = "operating" | "spac" | "etf_or_trust" | "re_listing";
export type EventStatus =
  | "listed" | "awaiting_ticker" | "registered_no_trade" | "no_price_data";

export interface ListingEvent {
  issuer_id: number;
  legal_name: string;
  ticker: string | null;
  exchange: string | null;
  sector: string | null;
  cohort: Cohort;
  cohort_reason: string | null;
  status: EventStatus;
  eight_a_filed_at: string;
  listed_on: string | null;
  open_price: string | null;
  ipo_price: string | null;
  day_one_pop: number | null;
  bars: number;
  mentions: number;
}

export interface Accuracy {
  name: string;
  metric: string;
  value: string;
  basis: string;
  caveat: string;
}

export interface Stats {
  issuers: number;
  filings: number;
  aliases: number;
  offerings: number;
  offerings_with_price: number;
  offerings_with_underwriters: number;
  listing_events: number;
  events_by_cohort: Record<string, number>;
  events_by_status: Record<string, number>;
  study_cohort: number;
  price_bars: number;
  mentions: number;
  mentions_needing_review: number;
  accuracy: Accuracy[];
  study: StudyResult;
}

export interface ReviewItem {
  mention_id: number;
  source: string;
  url: string | null;
  title: string | null;
  body_excerpt: string | null;
  posted_at: string;
  proposed_issuer_id: number;
  proposed_issuer_name: string;
  matched_alias: string | null;
  match_confidence: number | null;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export const getStats = () => get<Stats>("/api/v1/stats");

export const listEvents = (params: { cohort?: Cohort; status?: EventStatus; limit?: number } = {}) => {
  const q = new URLSearchParams();
  if (params.cohort) q.set("cohort", params.cohort);
  if (params.status) q.set("status", params.status);
  q.set("limit", String(params.limit ?? 100));
  return get<Page<ListingEvent>>(`/api/v1/events?${q}`);
};

export const listReviewQueue = (limit = 50) =>
  get<Page<ReviewItem>>(`/api/v1/review/queue?limit=${limit}`);

export interface StudyHorizon {
  horizon_days: number;
  n: number;
  n_with_attention: number;
  spearman_rho: number;
  permutation_p: number;
  median_return: number;
  underpowered: boolean;
}

export interface StudyResult {
  verdict: string;
  detail: string;
  attention_window_days: number;
  horizons: StudyHorizon[];
}
