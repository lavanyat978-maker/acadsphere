export function formatDate(value?: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTime(value?: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relativeTime(value?: string | null): string {
  if (!value) return "—";
  const delta = new Date(value).getTime() - Date.now();
  const minutes = Math.round(delta / 60000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 36) return formatter.format(hours, "hour");
  const days = Math.round(hours / 24);
  return formatter.format(days, "day");
}

export function titleCase(value?: string | null): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function initials(name?: string): string {
  if (!name) return "?";
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function bytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function statusTone(status?: string | null): "neutral" | "success" | "warning" | "danger" | "info" {
  switch (status) {
    case "PUBLISHED":
    case "STORED":
    case "COMPLETED":
    case "SECURED":
    case "PASSED":
    case "RELEASED":
      return "success";
    case "DRAFT":
    case "QUEUED":
    case "SUBMITTED":
    case "VALIDATING":
    case "ASSEMBLED":
    case "VALIDATED":
    case "ENCRYPTED":
    case "RELEASE_PENDING":
    case "DECRYPTED":
    case "PRINTED":
    case "PROCESSING":
    case "RUNNING":
    case "IN_PROGRESS":
      return "warning";
    case "FAILED":
    case "ARCHIVED":
    case "EXPIRED":
    case "CRITICAL":
      return "danger";
    case "AUTHORIZED":
    case "REAL_AI":
      return "info";
    default:
      return "neutral";
  }
}

export function difficultyTone(difficulty?: string): "success" | "warning" | "danger" {
  if (difficulty === "EASY") return "success";
  if (difficulty === "HARD") return "danger";
  return "warning";
}

export function scoreTone(percentage: number): "success" | "warning" | "danger" {
  if (percentage >= 75) return "success";
  if (percentage >= 50) return "warning";
  return "danger";
}

export function secondsToClock(totalSeconds: number): string {
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

const HONORIFICS = new Set(["dr", "prof", "mr", "mrs", "ms", "miss", "sir", "madam"]);

/** First given name, skipping titles such as "Dr." or "Prof.". */
export function firstName(fullName?: string | null, fallback = ""): string {
  const parts = (fullName ?? "").trim().split(/\s+/).filter(Boolean);
  const given = parts.find((part) => !HONORIFICS.has(part.replace(/\.$/, "").toLowerCase()));
  return given ?? parts[0] ?? fallback;
}
