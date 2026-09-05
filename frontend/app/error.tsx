"use client";

// This is the project's first Client Component, and it is one out of necessity
// rather than preference. React error boundaries are built on class components
// with lifecycle methods and an onClick retry -- state and event handlers, both
// of which only exist in the browser. There is no server equivalent, so Next
// requires "use client" on this file.
//
// That directive is the boundary itself: it marks where server-rendered output
// stops and shipped-and-hydrated JavaScript begins. Everything imported from
// here downward goes into the browser bundle, which is why the boundary is
// pushed as far down the tree as it will go. app/page.tsx stays on the server
// and only this fallback crosses over.
//
// Scope note, verified by testing rather than assumed: this boundary does NOT
// catch a Server Component that throws on a hard navigation. Nothing has
// committed at that point, so Next returns a bare 500 document and there is no
// hydrated React tree to render this into. It covers client-side navigations
// and post-hydration errors. app/page.tsx handles the unreachable-API case
// itself for exactly that reason.

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <div className="rounded-lg border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950/40">
        <h2 className="text-base font-semibold text-red-900 dark:text-red-200">
          Could not load issuers
        </h2>
        <p className="mt-2 text-sm text-red-800 dark:text-red-300">
          The API did not respond. Check that the backend is running on{" "}
          <code className="font-mono">:8000</code> — <code className="font-mono">docker compose up</code>.
        </p>
        {error.digest && (
          <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-400">
            digest: {error.digest}
          </p>
        )}
        <button
          onClick={reset}
          className="mt-4 rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500"
        >
          Retry
        </button>
      </div>
    </main>
  );
}
