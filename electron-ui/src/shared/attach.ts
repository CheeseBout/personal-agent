/** The `attachments` payload of a chat message: {name, mime, data(base64)}. Limits match the daemon's. */

export type Attachment = { name: string; mime: string; data: string };
export const MAX_BYTES = 5_000_000;
export const MAX_TOTAL = 15_000_000; // per message; the daemon refuses more
export const MAX_FILES = 4;
const EXTRA: Record<string, string> = { ".md": "text/markdown", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".webp": "image/webp", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".csv": "text/csv", ".txt": "text/plain",
  ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif" };

export function mimeOf(name: string, given = ""): string {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  return EXTRA[ext] || given || "application/octet-stream";
}

const bytesOf = (a: Attachment): number => Math.floor((a.data.length * 3) / 4);

/** Whether `a` still fits next to the files already attached (at most 4 files and 15 MB per message). */
export function roomFor(files: Attachment[], a: Attachment): boolean {
  return files.length < MAX_FILES && files.reduce((n, f) => n + bytesOf(f), 0) + bytesOf(a) <= MAX_TOTAL;
}

export function checkSize(name: string, size: number): void {
  if (size > MAX_BYTES) throw new Error(`${name} is larger than 5 MB`);
}
