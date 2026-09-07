import { ErrorPanel } from "@/app/components/Chrome";
import { listReviewQueue, type ReviewItem } from "@/lib/api";

export default async function Review() {
  let items: ReviewItem[] = [];
  let error: string | null = null;
  try {
    ({ data: items } = await listReviewQueue(50));
  } catch (cause) {
    error = cause instanceof Error ? cause.message : "Unknown error";
  }

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        Review queue
      </h1>
      <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        Matches the scorer proposed but would not accept. Rejecting one clears the issuer link and{" "}
        <em>keeps the row</em> — a human-confirmed non-match is the most valuable label this pipeline
        produces.
      </p>

      {error ? (
        <div className="mt-8"><ErrorPanel message={error} /></div>
      ) : items.length === 0 ? (
        <div className="mt-8 rounded-lg border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Nothing awaiting review. Every current match scored above the accept threshold or below
            the review floor.
          </p>
        </div>
      ) : (
        <ul className="mt-6 space-y-3">
          {items.map((m) => (
            <li key={m.mention_id} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                  {m.proposed_issuer_name}
                </span>
                <span className="font-mono text-xs text-zinc-500">
                  {m.match_confidence?.toFixed(2)} via “{m.matched_alias}” · {m.source}
                </span>
              </div>
              <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">{m.title ?? "(no title)"}</p>
              {m.body_excerpt && (
                <p className="mt-1 line-clamp-3 text-xs leading-5 text-zinc-500">{m.body_excerpt}</p>
              )}
              {m.url && (
                <a href={m.url} className="mt-2 inline-block text-xs text-sky-600 hover:underline dark:text-sky-400">
                  open source →
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
