import Link from "next/link";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/events", label: "Listings" },
  { href: "/issuers", label: "Issuers" },
  { href: "/review", label: "Review" },
];

export default function Nav() {
  return (
    <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/90 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/90">
      <nav className="mx-auto flex max-w-6xl items-center gap-1 overflow-x-auto px-4 py-3 sm:gap-2 sm:px-6">
        <Link href="/" className="mr-2 shrink-0 text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          IPO Surveillance
        </Link>
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="shrink-0 rounded-md px-2.5 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            {l.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
