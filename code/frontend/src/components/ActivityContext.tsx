import type { ReactNode } from "react";
import { createContext, useContext, useState, useCallback } from "react";

type ActivityType =
  | "idle"
  | "generating_response"
  | "generating_video"
  | "generating_image"
  | "generating_storyboard"
  | "generating_clip"
  | "rendering_video"
  | "transcribing"
  | "speaking"
  | "analyzing_images"
  | "fine_tuning"
  | "indexing_documents"
  | "custom";

interface ActivityState {
  type: ActivityType;
  label: string;
  detail?: string;
  progress?: number; // 0-100
}

const initialState: ActivityState = { type: "idle", label: "" };

interface ActivityContextValue {
  activity: ActivityState;
  startActivity: (type: ActivityType, label: string, detail?: string) => void;
  updateActivity: (partial: Partial<ActivityState>) => void;
  endActivity: () => void;
}

const ActivityContext = createContext<ActivityContextValue | null>(null);

export function ActivityProvider({ children }: { children: ReactNode }) {
  const [activity, setActivity] = useState<ActivityState>(initialState);

  const startActivity = useCallback((type: ActivityType, label: string, detail?: string) => {
    setActivity({ type, label, detail, progress: 0 });
  }, []);

  const updateActivity = useCallback((partial: Partial<ActivityState>) => {
    setActivity((prev) => ({ ...prev, ...partial }));
  }, []);

  const endActivity = useCallback(() => {
    setActivity(initialState);
  }, []);

  return (
    <ActivityContext.Provider value={{ activity, startActivity, updateActivity, endActivity }}>
      {children}
    </ActivityContext.Provider>
  );
}

export function useActivity() {
  const ctx = useContext(ActivityContext);
  if (!ctx) throw new Error("useActivity doit être utilisé dans un ActivityProvider");
  return ctx;
}

export function getActivityIcon(type: ActivityType): string {
  switch (type) {
    case "generating_response":
      return "💬";
    case "generating_video":
      return "🎬";
    case "generating_image":
      return "🖼️";
    case "generating_storyboard":
      return "📋";
    case "generating_clip":
      return "🎞️";
    case "rendering_video":
      return "🎥";
    case "transcribing":
      return "🎤";
    case "speaking":
      return "🔊";
    case "analyzing_images":
      return "👁️";
    case "fine_tuning":
      return "🧠";
    case "indexing_documents":
      return "📚";
    default:
      return "⚙️";
  }
}