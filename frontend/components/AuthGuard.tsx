"use client";
import { ReactNode, useEffect, useState } from "react";
import { getToken } from "@/lib/api";

/** Renders children only when a token exists; otherwise sends the visitor to the sign-in page.
 *  This is a convenience, not security: the API enforces auth on every request. */
export default function AuthGuard({ children }: { children: ReactNode }) {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (getToken()) setOk(true); else window.location.href = "/";
  }, []);
  return ok ? <>{children}</> : null;
}
