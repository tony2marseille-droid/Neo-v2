import { useActivity, getActivityIcon } from "./ActivityContext";

export default function ActivityIndicator() {
  const { activity } = useActivity();

  if (activity.type === "idle") return null;

  const icon = getActivityIcon(activity.type);
  const label = activity.label || (activity.type === "custom" ? "Traitement en cours…" : activity.type);

  return (
    <div
      className="activity-indicator"
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <div className="activity-content">
        <span className="activity-icon" aria-hidden="true">{icon}</span>
        <div className="activity-text">
          <span className="activity-label">{label}</span>
          {activity.detail && <span className="activity-detail">{activity.detail}</span>}
        </div>
        {typeof activity.progress === "number" && activity.progress > 0 && (
          <div className="activity-progress-wrap" aria-hidden="true">
            <div
              className="activity-progress"
              style={{ width: `${Math.min(100, Math.max(0, activity.progress))}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}