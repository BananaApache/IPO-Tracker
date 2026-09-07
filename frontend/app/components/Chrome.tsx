export function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">{value}</div>
      {sub && <div className="mt-1 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

const COHORT_STYLE: Record<string, string> = {
  operating: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/20",
  spac: "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/20",
  etf_or_trust: "bg-violet-50 text-violet-700 ring-violet-600/20 dark:bg-violet-950 dark:text-violet-300 dark:ring-violet-400/20",
  re_listing: "bg-zinc-100 text-zinc-600 ring-zinc-500/20 dark:bg-zinc-800 dark:text-zinc-400 dark:ring-zinc-400/20",
  listed: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-400/20",
};

export function Tag({ value, title }: { value: string; title?: string }) {
  const style = COHORT_STYLE[value] ??
    "bg-zinc-100 text-zinc-600 ring-zinc-500/20 dark:bg-zinc-800 dark:text-zinc-400 dark:ring-zinc-400/20";
  return (
    <span title={title} className={`inline-flex whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function Panel({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <section className="mt-8">
      <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-50">{title}</h2>
      {note && <p className="mt-1 text-sm leading-6 text-zinc-600 dark:text-zinc-400">{note}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950/40">
      <h2 className="text-base font-semibold text-red-900 dark:text-red-200">Could not load data</h2>
      <p className="mt-2 text-sm text-red-800 dark:text-red-300">
        Start the API with <code className="font-mono">docker compose up</code> and reload.
      </p>
      <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-400">{message}</p>
    </div>
  );
}
