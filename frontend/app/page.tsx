import Link from "next/link";

import { ErrorPanel, Panel, Stat, Tag } from "@/app/components/Chrome";
import { getStats, type Stats } from "@/lib/api";

export default async function Overview() {
  let stats: Stats | null = null;
  let error: string | null = null;
  try {
    stats = await getStats();
  } catch (cause) {
    error = cause instanceof Error ? cause.message : "Unknown error";
  }

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        IPO surveillance pipeline
      </h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        Tracks US IPOs from SEC registration through their first 90 days of trading, and tests
        whether elevated attention around a listing predicts underperformance from the opening
        price. Measurement tool, not a recommender.
      </p>

      {error ? (
        <div className="mt-8">
          <ErrorPanel message={error} />
        </div>
      ) : (
        stats && (
          <>
            <Panel
              title="Measured accuracy"
              note="Each number comes from a hand-labelled evaluation, scored before tuning. The caveats are part of the result."
            >
              <div className="grid gap-3 sm:grid-cols-3">
                {stats.accuracy.map((a) => (
                  <div key={a.name} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
                    <div className="text-xs uppercase tracking-wide text-zinc-500">{a.name}</div>
                    <div className="mt-1 flex items-baseline gap-2">
                      <span className="text-3xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                        {a.value}
                      </span>
                      <span className="text-xs text-zinc-500">{a.metric}</span>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-zinc-600 dark:text-zinc-400">{a.basis}</p>
                    <p className="mt-2 border-t border-zinc-100 pt-2 text-xs leading-5 text-amber-700 dark:border-zinc-800 dark:text-amber-400">
                      {a.caveat}
                    </p>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="Pipeline">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Issuers" value={stats.issuers.toLocaleString()} sub="from EDGAR" />
                <Stat label="Filings" value={stats.filings.toLocaleString()} />
                <Stat label="Aliases" value={stats.aliases.toLocaleString()} sub="for matching" />
                <Stat label="Price bars" value={stats.price_bars.toLocaleString()} sub="Polygon, unadjusted" />
                <Stat
                  label="Offerings"
                  value={stats.offerings.toLocaleString()}
                  sub={`${stats.offerings_with_price} with a price, ${stats.offerings_with_underwriters} with underwriters`}
                />
                <Stat label="Listing events" value={stats.listing_events.toLocaleString()} sub="from 8-A filings" />
                <Stat label="Study cohort" value={stats.study_cohort.toLocaleString()} sub="operating, confirmed trading" />
                <Stat
                  label="Mentions"
                  value={stats.mentions.toLocaleString()}
                  sub={`${stats.mentions_needing_review} awaiting review`}
                />
              </div>
            </Panel>

            <Panel
              title="Listing event cohorts"
              note="Classification is stored, not applied as a filter, so every excluded cohort stays queryable."
            >
              <div className="flex flex-wrap gap-2">
                {Object.entries(stats.events_by_cohort).map(([k, v]) => (
                  <Link key={k} href={`/events?cohort=${k}`} className="group">
                    <span className="inline-flex items-center gap-2 rounded-lg border border-zinc-200 px-3 py-2 text-sm group-hover:bg-zinc-50 dark:border-zinc-800 dark:group-hover:bg-zinc-900">
                      <Tag value={k} />
                      <span className="tabular-nums font-medium text-zinc-900 dark:text-zinc-100">{v}</span>
                    </span>
                  </Link>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(stats.events_by_status).map(([k, v]) => (
                  <span key={k} className="inline-flex items-center gap-2 rounded-lg border border-dashed border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800">
                    <Tag value={k} />
                    <span className="tabular-nums text-zinc-700 dark:text-zinc-300">{v}</span>
                  </span>
                ))}
              </div>
            </Panel>

            <Panel title="Event study" note="In progress. The constraint is sample size, not code.">
              <div className="rounded-lg border border-zinc-200 p-4 text-sm leading-6 text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
                <p>
                  An event study needs attention data on <em>both</em> sides of the listing date and a
                  return horizon after it. Joint availability is what bounds the sample, and it is
                  currently small:
                </p>
                <ul className="mt-3 list-disc space-y-1 pl-5 text-zinc-600 dark:text-zinc-400">
                  <li>
                    <strong className="text-zinc-900 dark:text-zinc-100">88</strong> operating-company
                    listings confirmed trading.
                  </li>
                  <li>
                    A 90-day return needs a listing at least 90 days old, and attention ±14 days around
                    it then falls before a 90-day corpus begins — so that cell was{" "}
                    <strong className="text-zinc-900 dark:text-zinc-100">structurally zero</strong>, not
                    unlucky.
                  </li>
                  <li>
                    With the corpus extended to 210 days the ceiling is 74 / 59 / 25 at 30 / 60 / 90 days.
                  </li>
                </ul>
                <p className="mt-3">
                  A null result is the expected publishable outcome. The window and threshold are fixed
                  in advance and will not be tuned to find a relationship.
                </p>
              </div>
            </Panel>
          </>
        )
      )}
    </main>
  );
}
