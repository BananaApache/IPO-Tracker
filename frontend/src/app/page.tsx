import Search from './components/Search';
import AutoRefresh from './components/AutoRefresh';

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ search?: string }>;
}) {
  const { search } = await searchParams;
  const query = search || '';
  
  // const res = await fetch(`http://web:8000/api/ipos/?search=${query}`, {
  //   cache: 'no-store', // !!! pretty important thing in system design!
  // });
  const res = await fetch(`http://web:8000/api/ipos/`, {
    next: { revalidate: 3600 }, // revalidate every hour
  });
  const data = await res.json();

  return (
    <main className="p-8">
      <AutoRefresh />
      <h1 className="text-3xl font-bold mb-6">IPO Tracker</h1>
      <Search />
      <div className="grid gap-4">
        {data.results.map((ipo: any) => (
          <div key={ipo.id} className="p-4 border rounded shadow-sm bg-white text-black">
            <h2 className="font-bold">{ipo.company_name} ({ipo.ticker})</h2>
            <p className="text-gray-600">{ipo.sector}</p>
          </div>
        ))}
      </div>
    </main>
  );
}