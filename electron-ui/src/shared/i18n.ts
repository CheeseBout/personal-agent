/** Vietnamese + English UI strings: tr("Send", "Gửi"); placeholders via tr("{n} items", "{n} mục", { n: 3 }).
 * The English text is also the fallback, so a missing Vietnamese string never shows an empty label. */

let lang: "en" | "vi" = "en";

/** 'auto' follows the OS UI language; anything but vi falls back to English. */
export function resolveLanguage(setting: string, osLocale = ""): "en" | "vi" {
  if (setting === "vi" || setting === "en") return setting;
  return osLocale.toLowerCase().startsWith("vi") ? "vi" : "en";
}

export function setLanguage(setting: string, osLocale = ""): "en" | "vi" {
  return (lang = resolveLanguage(setting, osLocale));
}

export const language = (): "en" | "vi" => lang;

export function tr(en: string, vi?: string | null, fmt?: Record<string, unknown>): string {
  const text = lang === "vi" && vi ? vi : en;
  return fmt ? text.replace(/\{(\w+)\}/g, (m, k) => (k in fmt ? String(fmt[k]) : m)) : text;
}
