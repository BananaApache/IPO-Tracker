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

            <Panel
              title="Event study result"
              note="Does elevated attention around a listing predict underperformance from the opening price? Window and horizons were fixed before the data was seen."
            >
              <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/40">
                <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">
                  {stats.study.verdict}
                </p>
                <p className="mt-2 text-sm leading-6 text-amber-800 dark:text-amber-300">
                  {stats.study.detail}
                </p>
              </div>

              <div className="mt-3 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
                    <tr>
                      <th scope="col" className="px-3 py-2.5 font-medium">Horizon</th>
                      <th scope="col" className="px-3 py-2.5 text-right font-medium">n</th>
                      <th scope="col" className="px-3 py-2.5 text-right font-medium">with attention</th>
                      <th scope="col" className="px-3 py-2.5 text-right font-medium">Spearman ρ</th>
                      <th scope="col" className="px-3 py-2.5 text-right font-medium">p</th>
                      <th scope="col" className="px-3 py-2.5 text-right font-medium">median return</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                    {stats.study.horizons.map((h) => (
                      <tr key={h.horizon_days}>
                        <td className="px-3 py-2.5 font-medium text-zinc-900 dark:text-zinc-100">
                          {h.horizon_days} days
                          {h.underpowered && (
                            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                              underpowered
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{h.n}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-500">{h.n_with_attention}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-500">{h.spearman_rho.toFixed(3)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-500">{h.permutation_p.toFixed(3)}</td>
                        <td className={`px-3 py-2.5 text-right tabular-nums ${h.median_return < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
                          {(h.median_return * 100).toFixed(1)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="mt-3 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                One result here <em>is</em> well powered, and it is not about attention:{" "}
                <strong className="text-zinc-900 dark:text-zinc-100">
                  buying at the opening price lost money at the median over every horizon
                </strong>{" "}
                — −8.5% at 30 days (n=171), −15.9% at 60 (n=157), −15.7% at 90 (n=112), with
                63–68% of listings negative. That is a descriptive fact about this window, not a
                test of the thesis.
              </p>
              <p className="mt-2 text-xs text-zinc-500">
                ±{stats.study.attention_window_days}-day attention window, Spearman rank
                correlation, 10,000-permutation p. Reproduce with{" "}
                <code className="font-mono">python -m backend.study</code>. Returns are derived
                from the stored bar series, never cached.
              </p>
            </Panel>
          </>
        )
      )}
    </main>
  );
}
