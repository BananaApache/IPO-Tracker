'use client'; 

import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // send this later to a logging service like Sentry!!!!
    console.error('System Failure:', error);
  }, [error]);

  return (
    <div className="p-8 text-center">
      <h2 className="text-2xl font-bold text-red-600">Data Connection Interrupted</h2>
      <p className="text-gray-600 mb-4">
        We are having trouble reaching our IPO data engine.
      </p>
      <button
        onClick={() => reset()}
        className="bg-blue-600 text-white px-6 py-2 rounded shadow-md"
      >
        Try Reconnecting
      </button>
    </div>
  );
}