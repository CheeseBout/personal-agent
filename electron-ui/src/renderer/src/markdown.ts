/** Markdown -> safe HTML. The text is untrusted (model output, web pages, files): raw HTML is never parsed, and the result is
 * sanitised again, so nothing in a transcript can run script, load a remote resource or draw a fake form. */
import DOMPurify from "dompurify";
import MarkdownIt from "markdown-it";

const md = new MarkdownIt({ html: false, linkify: true });

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(md.render(text), { FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select", "iframe", "object", "embed"],
    FORBID_ATTR: ["style", "srcset"], ALLOW_DATA_ATTR: false });
}
