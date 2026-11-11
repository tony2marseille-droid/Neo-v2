/** Notification ephemere (zone de toasts de App). */
export type ToastKind = "info" | "error" | "success";

/** Signature commune du `toast` transmis aux vues. */
export type ToastFn = (msg: string, kind?: ToastKind) => void;
