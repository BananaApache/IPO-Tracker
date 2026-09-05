import { listIssuers, type Issuer, type IssuerStatus } from "@/lib/api";

// A Server Component: an async function that runs only on the server and ships
// its rendered output, not its code. There is no useState, no useEffect, and no
// loading spinner here because there is no client-side request to wait for --
// the fetch below happens before this HTML exists.
//
// Note what that buys us: API_BASE_URL never reaches the browser, the browser
// never learns the backend's address, and this route ships 0 kB of JavaScript
// for the table itself.

const STATUS_STYLES: Record<IssuerStatus, string> = {
  filed: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-400/20",
  priced: "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/20",
  listed: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/20",
  withdrawn: "bg-zinc-100 text-zinc-600 ring-zinc-500/20 dark:bg-zinc-800 dark:text-zinc-400 dark:ring-zinc-400/20",
};

function formatFiledAt(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC", // Filing dates are SEC business dates, not local time.
  });
}

export default async function Home() {
  // The backend being unreachable is an expected operational state for a
  // dashboard, not an exceptional one, so it is handled here rather than left
  // to app/error.tsx.
  //
  // That distinction is load-bearing and was verified, not assumed: error.tsx
  // is a *client* boundary. When this Server Component throws on a hard
  // navigation, nothing has committed yet, so Next returns a bare 500 document
  // and the boundary never renders. It covers client-side navigations and
  // post-hydration errors -- which is why it is still here -- but it cannot
  // cover the first paint. Catching here renders a useful page with zero
  // JavaScript.
  let issuers: Issuer[] = [];
  let loadError: string | null = null;

  try {
    ({ data: issuers } = await listIssuers({ limit: 25 }));
  } catch (cause) {
    loadError = cause instanceof Error ? cause.message : "Unknown error";
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Registered issuers
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
          Companies with an active registration statement on file with the SEC.
          Surveillance only — this page reports what has been filed publicly and
          makes no assessment of any security.
        </p>
      </header>

      {loadError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950/40">
          <h2 className="text-base font-semibold text-red-900 dark:text-red-200">
            Could not load issuers
          </h2>
          <p className="mt-2 text-sm text-red-800 dark:text-red-300">
            The API did not respond. Start it with{" "}
            <code className="font-mono">docker compose up</code> and reload.
          </p>
          <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-400">{loadError}</p>
        </div>
      ) : issuers.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 p-12 text-center dark:border-zinc-700">
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            No issuers yet. Run{" "}
            <code className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs dark:bg-zinc-800">
              uv run python -m backend.seed
            </code>{" "}
            to load the starter set.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">Company</th>
                <th scope="col" className="px-4 py-3 font-medium">CIK</th>
                <th scope="col" className="px-4 py-3 font-medium">Sector</th>
                <th scope="col" className="px-4 py-3 font-medium">Exchange</th>
                <th scope="col" className="px-4 py-3 font-medium">Status</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">First filed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {issuers.map((issuer) => (
                <tr key={issuer.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
                  <td className="px-4 py-3 font-medium text-zinc-900 dark:text-zinc-100">
                    {issuer.legal_name}
                    {issuer.ticker && (
                      <span className="ml-2 font-mono text-xs text-zinc-500">
                        {issuer.ticker}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-500 dark:text-zinc-400">
                    {issuer.cik}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {issuer.sector ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {issuer.exchange ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_STYLES[issuer.status]}`}
                    >
                      {issuer.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-zinc-600 dark:text-zinc-400">
                    {formatFiledAt(issuer.first_filed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loadError && (
        <p className="mt-4 text-xs text-zinc-500">
          {issuers.length} issuer{issuers.length === 1 ? "" : "s"} shown.
        </p>
      )}
    </main>
  );
}
