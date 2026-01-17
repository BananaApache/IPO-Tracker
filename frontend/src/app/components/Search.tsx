
'use client';

import { useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

export default function Search() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(searchParams.get('search') || '');

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    router.push(`/?search=${query}`);
  };

  return (
    <form onSubmit={handleSearch} className="mb-8">
      <input
        type="text"
        placeholder="Search ticker, company, or sector..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        className="w-full p-2 border border-gray-300 rounded text-black"
      />
      <button type="submit" className="mt-2 bg-blue-600 text-white px-4 py-2 rounded">
        Search
      </button>
    </form>
  );
}
