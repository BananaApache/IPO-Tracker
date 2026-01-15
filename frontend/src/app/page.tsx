export default async function Home() {
  // Fetching data directly on the server!
  const res = await fetch('http://web:8000/api/ipos/', { cache: 'no-store' });
  const data = await res.json();

  return (
    <main className="p-8">
      <h1 className="text-2xl font-bold mb-4">Latest IPOs</h1>
      <div className="grid gap-4">
        {data.results.map((ipo: any) => (
          <div key={ipo.id} className="p-4 border rounded shadow-sm">
            <h2 className="font-bold">{ipo.company_name} ({ipo.ticker})</h2>
            <p className="text-gray-600">{ipo.sector} • ${ipo.price}</p>
          </div>
        ))}
      </div>
    </main>
  );
}