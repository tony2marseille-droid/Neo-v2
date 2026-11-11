import type { Conversation } from "../lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  conversations: Conversation[];
  selectedConv: number | null;
  onSelectConv: (id: number) => void;
  onDeleteConv: (id: number) => void;
  onClearAll: () => void;
  onNewChat: () => void;
}

/** "2026-11-04T01:23:04" -> "04/11 01:23" (annee ajoutee si differente). */
function quand(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
  const deux = (n: number) => String(n).padStart(2, "0");
  const heure = `${deux(d.getHours())}:${deux(d.getMinutes())}`;
  const jour = `${deux(d.getDate())}/${deux(d.getMonth() + 1)}`;
  const cetteAnnee = d.getFullYear() === new Date().getFullYear();
  return cetteAnnee ? `${jour} ${heure}` : `${jour}/${d.getFullYear()} ${heure}`;
}

/**
 * Panneau lateral masquable : l'historique des conversations stockees
 * (date/heure de derniere activite, provenance Telegram, vidage global).
 */
export default function HistoryPanel({
  open,
  onClose,
  conversations,
  selectedConv,
  onSelectConv,
  onDeleteConv,
  onClearAll,
  onNewChat,
}: Props) {
  return (
    <aside className={`history-panel ${open ? "open" : ""}`}>
      <div className="history-head">
        <span>Historique</span>
        <button onClick={onClose} title="Masquer l'historique" aria-label="Masquer l'historique">
          ✕
        </button>
      </div>
      <button className="primary" onClick={onNewChat}>
        Nouveau chat
      </button>
      <div className="conv-list">
        {conversations.length === 0 && (
          <div className="conv-item" style={{ cursor: "default", opacity: 0.5 }}>
            Aucune conversation
          </div>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === selectedConv ? "selected" : ""}`}
            onClick={() => onSelectConv(c.id)}
            onKeyDown={(e) => {
              if (e.target !== e.currentTarget) return;
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelectConv(c.id);
              }
            }}
            role="button"
            tabIndex={0}
            aria-current={c.id === selectedConv ? "true" : undefined}
            title={c.title}
          >
            <div className="conv-main">
              <span className="conv-title">{c.title}</span>
              <span className="conv-meta">
                <span className="conv-date">{quand(c.updated_at)}</span>
                {Boolean(c.telegram) && (
                  <span className="conv-badge-tg" title="Conversation pilotee par Telegram">
                    Telegram
                  </span>
                )}
                {typeof c.message_count === "number" && c.message_count > 0 && (
                  <span className="conv-count">{c.message_count} msg</span>
                )}
              </span>
            </div>
            <button
              className="conv-del"
              title="Supprimer"
              aria-label={`Supprimer la conversation ${c.title}`}
              onClick={(e) => {
                e.stopPropagation();
                onDeleteConv(c.id);
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
      {conversations.length > 0 && (
        <button className="danger history-clear" onClick={onClearAll}>
          Vider tout l'historique
        </button>
      )}
    </aside>
  );
}
