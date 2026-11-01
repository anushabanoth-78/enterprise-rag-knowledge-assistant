import { Citation } from "@/lib/api";

type Props = {
  citations: Citation[];
  active: number | null;
  idPrefix: string;
  onOpen: (c: Citation) => void;
};

export default function CitationViewer({ citations, active, idPrefix, onOpen }: Props) {
  if (citations.length === 0) return null;
  return (
    <div className="sources">
      {citations.map(c => (
        <div key={c.ref} id={`${idPrefix}-${c.ref}`} className={`passage ${active === c.ref ? "active" : ""}`}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted">Source {c.ref}: {c.filename}, page {c.page} (relevance {c.score})</span>
            <button className="ghost small" onClick={() => onOpen(c)}>Open page {c.page}</button>
          </div>
          <div className="body">{c.text}</div>
        </div>
      ))}
    </div>
  );
}
