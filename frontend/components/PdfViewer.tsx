"use client";
import { useEffect, useState } from "react";
import { apiBlob } from "@/lib/api";

export type ViewerTarget = { docId: string; filename: string; page: number };

/** Opens the original PDF at the cited page. The file endpoint needs the bearer token, so the PDF is
 *  fetched as a blob and shown through an object URL (an <iframe src> cannot send headers). */
export default function PdfViewer({ target, onClose }: { target: ViewerTarget; onClose: () => void }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setSrc(null); setError("");
    apiBlob(`/api/documents/${target.docId}/file`)
      .then(blob => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setSrc(`${url}#page=${target.page}`);
      })
      .catch(e => !cancelled && setError((e as Error).message));
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [target.docId, target.page]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={`${target.filename}, page ${target.page}`}>
      <div className="modal">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <strong>{target.filename}, page {target.page}</strong>
          <button className="ghost" onClick={onClose}>Close</button>
        </div>
        {error ? <p className="error" role="alert">{error}</p>
          : src ? <iframe title={target.filename} src={src} />
          : <p className="muted">Loading document…</p>}
      </div>
    </div>
  );
}
