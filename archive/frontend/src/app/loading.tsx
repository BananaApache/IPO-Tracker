export default function Loading() {
  return (
    <div className="p-8 space-y-4">
      <div className="h-8 w-48 bg-gray-200 animate-pulse rounded"></div>
      <div className="space-y-3">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-24 w-full bg-gray-100 animate-pulse rounded"></div>
        ))}
      </div>
      <p className="text-gray-500">Fetching the latest IPO data...</p>
    </div>
  );
}