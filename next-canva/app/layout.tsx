import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Events / Canva automation",
  description: "Queue Canva designs for Sheets and Drive delivery.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return <html lang="en"><body>{children}</body></html>;
}
