import { ErrorPanel, Tag } from "@/app/components/Chrome";
import { listEvents, type Cohort, type ListingEvent } from "@/lib/api";

function fmtDate(v: string | null) {
  if (!v) return "—";
  return new Date(v).toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
  });
}
function fmtMoney(v: string | null) {
  return v === null ? "—" : `$${Number(v).toFixed(2)}`;
}

export default async function Events({
  searchParams,
}: {
  searchParams: Promise<{ cohort?: string }>;
}) {
  const { cohort } = await searchParams;
  let events: ListingEvent[] = [];
  let error: string | null = null;
  try {
    ({ data: events } = await listEvents({ cohort: cohort as Cohort | undefined, limit: 200 }));
  } catch (cause) {
    error = cause instanceof Error ? cause.message : "Unknown error";
  }

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        Listing events
      </h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        Detected from <code className="font-mono text-xs">8-A</code> exchange registrations paired
        with a registration statement. The listing date is the first price bar, never the 8-A date.
        {cohort && <> Filtered to <Tag value={cohort} />.</>}
      </p>

      {error ? (
        <div className="mt-8"><ErrorPanel message={error} /></div>
      ) : (
        <>
          <div className="mt-6 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
                <tr>
                  <th scope="col" className="px-3 py-3 font-medium">Company</th>
                  <th scope="col" className="px-3 py-3 font-medium">Ticker</th>
                  <th scope="col" className="px-3 py-3 font-medium">Cohort</th>
                  <th scope="col" className="px-3 py-3 font-medium">Status</th>
                  <th scope="col" className="px-3 py-3 font-medium">8-A</th>
                  <th scope="col" className="px-3 py-3 font-medium">Listed</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">Open</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">IPO px</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">Pop</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">Bars</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">Mentions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {events.map((e) => (
                  <tr key={e.issuer_id} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
                    <td className="max-w-[15rem] truncate px-3 py-2.5 font-medium text-zinc-900 dark:text-zinc-100">
                      {e.legal_name}
                      {e.sector && <div className="truncate text-xs font-normal text-zinc-500">{e.sector}</div>}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                      {e.ticker ?? "—"}
                      {e.exchange && <div className="text-[11px] text-zinc-400">{e.exchange}</div>}
                    </td>
                    <td className="px-3 py-2.5"><Tag value={e.cohort} title={e.cohort_reason ?? undefined} /></td>
                    <td className="px-3 py-2.5"><Tag value={e.status} /></td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-zinc-600 dark:text-zinc-400">{fmtDate(e.eight_a_filed_at)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-zinc-600 dark:text-zinc-400">{fmtDate(e.listed_on)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-zinc-700 dark:text-zinc-300">{fmtMoney(e.open_price)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-zinc-500">{fmtMoney(e.ipo_price)}</td>
                    <td className={`px-3 py-2.5 text-right tabular-nums ${
                      e.day_one_pop === null ? "text-zinc-400"
                      : e.day_one_pop >= 0 ? "text-emerald-600 dark:text-emerald-400"
                      : "text-red-600 dark:text-red-400"}`}>
                      {e.day_one_pop === null ? "—" : `${(e.day_one_pop * 100).toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-zinc-500">{e.bars}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-zinc-500">{e.mentions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-4 text-xs text-zinc-500">
            {events.length} event{events.length === 1 ? "" : "s"} shown. Hover a cohort tag for why
            it was classified that way. IPO price and pop are blank where prospectus extraction found
            no price — 26% fill rate, deliberately not guessed.
          </p>
        </>
      )}
    </main>
  );
}
