import type { Metadata } from "next";
import { Dashboard } from "@/components/dashboard/dashboard";

export const metadata: Metadata = {
  title: "Dashboard",
  description: "Period close status, live agent runs and the learning curve.",
};

export default function DashboardPage() {
  return <Dashboard />;
}
