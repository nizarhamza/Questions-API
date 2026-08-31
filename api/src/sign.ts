// Signed "deals" for sealed questions.
//
// In sealed mode the client is handed options in a per-request order with no answer
// index. The deal is an HMAC over (question, shuffled correct position, expiry), so
// /v1/check can score the answer without the server storing anything and without the
// client being able to forge a position or replay a stale one.

const encoder = new TextEncoder();
const keyCache = new Map<string, CryptoKey>();

async function hmacKey(secret: string): Promise<CryptoKey> {
  const cached = keyCache.get(secret);
  if (cached) return cached;
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
  keyCache.set(secret, key);
  return key;
}

function base64url(bytes: ArrayBuffer): string {
  let binary = "";
  for (const b of new Uint8Array(bytes)) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export interface DealPayload {
  /** Global bank index of the question. */
  index: number;
  /** Correct option's position in the order the client was shown. */
  position: number;
  /** Unix seconds. */
  expires: number;
}

export async function signDeal(payload: DealPayload, secret: string): Promise<string> {
  const body = `${payload.index}.${payload.position}.${payload.expires}`;
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(body));
  return `${body}.${base64url(sig)}`;
}

export type DealResult =
  | { ok: true; payload: DealPayload }
  | { ok: false; reason: "malformed" | "bad_signature" | "expired" };

export async function verifyDeal(deal: string, secret: string): Promise<DealResult> {
  const parts = deal.split(".");
  if (parts.length !== 4) return { ok: false, reason: "malformed" };

  const [indexRaw, positionRaw, expiresRaw, sig] = parts as [string, string, string, string];
  const index = Number(indexRaw);
  const position = Number(positionRaw);
  const expires = Number(expiresRaw);
  if (!Number.isInteger(index) || !Number.isInteger(position) || !Number.isInteger(expires)) {
    return { ok: false, reason: "malformed" };
  }

  const body = `${indexRaw}.${positionRaw}.${expiresRaw}`;
  const expected = await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(body));
  // Compare before the expiry check so a forged deal and a stale one take the same path.
  if (base64url(expected) !== sig) return { ok: false, reason: "bad_signature" };
  if (Math.floor(Date.now() / 1000) > expires) return { ok: false, reason: "expired" };

  return { ok: true, payload: { index, position, expires } };
}
