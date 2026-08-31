// OpenTDB's four output encodings, reproduced so existing clients decode our
// responses with the code they already have.

export type EncodeMode = "html" | "url3986" | "base64" | "legacy";

export function resolveEncoding(input: string | null): EncodeMode | undefined {
  if (input === null || input === "") return "html"; // OpenTDB's default
  switch (input.toLowerCase()) {
    case "html":
    case "html3986": return "html";
    case "url3986": return "url3986";
    case "base64": return "base64";
    case "legacy":
    case "urllegacy": return "legacy";
    default: return undefined;
  }
}

/** PHP htmlspecialchars(ENT_QUOTES) — what OpenTDB emits when no encoding is asked for. */
function htmlSpecialChars(input: string): string {
  return input
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/** PHP rawurlencode: RFC 3986, so space becomes %20 and ~ stays literal. */
function rawUrlEncode(input: string): string {
  return encodeURIComponent(input).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

/** PHP urlencode: the older form, where space becomes +. */
function legacyUrlEncode(input: string): string {
  return rawUrlEncode(input).replace(/%20/g, "+");
}

function toBase64(input: string): string {
  const bytes = new TextEncoder().encode(input);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

export function encodeText(input: string, mode: EncodeMode): string {
  switch (mode) {
    case "html": return htmlSpecialChars(input);
    case "url3986": return rawUrlEncode(input);
    case "legacy": return legacyUrlEncode(input);
    case "base64": return toBase64(input);
  }
}
