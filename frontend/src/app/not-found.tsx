import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";

export default function NotFound() {
  return (
    <section className="mx-auto flex max-w-xl flex-col items-start gap-4 rounded-xl border border-border bg-surface p-8">
      <p className="font-mono text-xs text-muted">404 · Page not found</p>
      <h1 className="text-2xl font-semibold">This page is unavailable</h1>
      <p className="text-sm text-muted">Return to the dashboard to view period closes, exceptions, and financial memory.</p>
      <Link href="/" className={buttonVariants()}>Back to dashboard</Link>
    </section>
  );
}
