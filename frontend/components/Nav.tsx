"use client";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getToken, logout, Me } from "@/lib/api";

export default function Nav() {
  const path = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    if (path !== "/" && getToken()) api<Me>("/api/auth/me").then(setMe).catch(() => setMe(null));
  }, [path]);

  const link = (href: string, label: string) => (
    <a href={href} aria-current={path === href ? "page" : undefined}>{label}</a>
  );
  return (
    <nav>
      <strong>Knowledge Assistant</strong>
      {path !== "/" && (<>
        {link("/library", "Library")}
        {link("/chat", "Ask")}
        {link("/compare", "Compare models")}
        {me?.is_admin && link("/admin", "Admin")}
        {me?.is_admin && link("/evaluation", "Evaluation")}
        <span className="muted">{me?.email}</span>
        <button className="ghost" onClick={logout}>Sign out</button>
      </>)}
    </nav>
  );
}
