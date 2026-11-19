import { Inbox } from "lucide-react";

export default function DashboardPlaceholder() {
  return (
    <section
      aria-labelledby="dashboard-empty-title"
      className="flex flex-col items-center gap-6 rounded-xl border border-dashed border-gray-300 p-12 text-center text-gray-500"
    >
      <Inbox className="h-16 w-16" aria-hidden="true" />
      <h1 id="dashboard-empty-title" className="text-xl font-semibold">
        Dashboard NOVA AGENT
      </h1>
      <p className="max-w-md" role="status">
        Fondasi aplikasi sedang disiapkan. Belum ada organisasi atau aktivitas untuk ditampilkan.
      </p>
    </section>
  );
}
