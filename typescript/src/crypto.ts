import { createHash, generateKeyPairSync, sign, verify, type KeyObject } from "node:crypto";

export function sha256(data: string): string {
  return createHash("sha256").update(data, "utf-8").digest("hex");
}

export function sha256Bytes(data: Buffer): Buffer {
  return createHash("sha256").update(data).digest();
}

export function computeDataHash(data: string): string {
  return sha256(data);
}

export function computeEntryHash(
  seq: number,
  ts: string,
  eventType: string,
  agent: string,
  dataHash: string,
  prevHash: string,
): string {
  const payload = `${seq}|${ts}|${eventType}|${agent}|${dataHash}|${prevHash}`;
  return sha256(payload);
}

export function generateEd25519KeyPair(): {
  publicKey: KeyObject;
  privateKey: KeyObject;
} {
  return generateKeyPairSync("ed25519");
}

export function signEntry(entryHash: string, privateKey: KeyObject): string {
  const sig = sign(null, Buffer.from(entryHash, "utf-8"), privateKey);
  return sig.toString("hex");
}

export function verifySignature(
  entryHash: string,
  signature: string,
  publicKey: KeyObject,
): boolean {
  return verify(
    null,
    Buffer.from(entryHash, "utf-8"),
    publicKey,
    Buffer.from(signature, "hex"),
  );
}

export function exportPublicKey(key: KeyObject): string {
  return key.export({ type: "spki", format: "pem" }) as string;
}

export function exportPrivateKey(key: KeyObject): string {
  return key.export({ type: "pkcs8", format: "pem" }) as string;
}
