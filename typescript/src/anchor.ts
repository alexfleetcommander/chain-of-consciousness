import { readFileSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import { request as httpsRequest } from "node:https";
import type { AnchorResult, TsaStatus } from "./types.js";

const DEFAULT_TSA_SERVERS = [
  { name: "freeTSA", url: "https://freetsa.org/tsr" },
];

const DEFAULT_OTS_CALENDARS = [
  "https://a.pool.opentimestamps.org",
  "https://b.pool.opentimestamps.org",
  "https://a.pool.eternitywall.com",
];

export function computeChainHash(path: string): string {
  const data = readFileSync(path);
  return createHash("sha256").update(data).digest("hex");
}

function derTagLength(tag: number, content: Buffer): Buffer {
  const length = content.length;
  let header: Buffer;
  if (length < 0x80) {
    header = Buffer.from([tag, length]);
  } else if (length < 0x100) {
    header = Buffer.from([tag, 0x81, length]);
  } else {
    header = Buffer.from([
      tag,
      0x82,
      (length >> 8) & 0xff,
      length & 0xff,
    ]);
  }
  return Buffer.concat([header, content]);
}

export function buildRfc3161Tsq(hashBytes: Buffer): Buffer {
  const sha256Oid = derTagLength(
    0x06,
    Buffer.from([0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01]),
  );
  const algId = derTagLength(
    0x30,
    Buffer.concat([sha256Oid, Buffer.from([0x05, 0x00])]),
  );
  const msgImprint = derTagLength(
    0x30,
    Buffer.concat([algId, derTagLength(0x04, hashBytes)]),
  );

  const version = derTagLength(0x02, Buffer.from([0x01]));

  let nonceRaw = randomBytes(8);
  if (nonceRaw[0] & 0x80) {
    nonceRaw = Buffer.concat([Buffer.from([0x00]), nonceRaw]);
  }
  const nonce = derTagLength(0x02, nonceRaw);

  const certReq = derTagLength(0x01, Buffer.from([0xff]));

  return derTagLength(
    0x30,
    Buffer.concat([version, msgImprint, nonce, certReq]),
  );
}

export function parseTsrStatus(tsrBytes: Buffer): TsaStatus {
  const STATUS_NAMES: Record<number, string> = {
    0: "granted",
    1: "grantedWithMods",
    2: "rejection",
    3: "waiting",
    4: "revocationWarning",
    5: "revocationNotification",
  };

  function readTl(data: Buffer, off: number): [number, number, number] {
    const tag = data[off];
    off++;
    const lb = data[off];
    off++;
    if (lb < 0x80) {
      return [tag, lb, off];
    }
    const n = lb & 0x7f;
    let length = 0;
    for (let i = 0; i < n; i++) {
      length = (length << 8) | data[off];
      off++;
    }
    return [tag, length, off];
  }

  try {
    const [, outerLen, outerStart] = readTl(tsrBytes, 0);
    const outerEnd = outerStart + outerLen;
    const [, siLen, siStart] = readTl(tsrBytes, outerStart);
    const siEnd = siStart + siLen;
    const [tag, intLen, intStart] = readTl(tsrBytes, siStart);

    if (tag !== 0x02) {
      return {
        status: -1,
        status_text: "parse_error: expected INTEGER",
        has_token: false,
        tsr_size: tsrBytes.length,
      };
    }

    const statusSlice = tsrBytes.subarray(intStart, intStart + intLen);
    let statusVal = 0;
    for (let i = 0; i < statusSlice.length; i++) {
      statusVal = (statusVal << 8) | statusSlice[i];
    }

    return {
      status: statusVal,
      status_text: STATUS_NAMES[statusVal] ?? `unknown(${statusVal})`,
      has_token: siEnd < outerEnd,
      tsr_size: tsrBytes.length,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      status: -1,
      status_text: `parse_error: ${msg}`,
      has_token: false,
      tsr_size: tsrBytes.length,
    };
  }
}

export function submitTsa(
  hashHex: string,
  serverUrl = "https://freetsa.org/tsr",
  timeout = 30000,
): Promise<AnchorResult> {
  return new Promise((resolve) => {
    const hashBytes = Buffer.from(hashHex, "hex");
    const tsq = buildRfc3161Tsq(hashBytes);

    const url = new URL(serverUrl);
    const req = httpsRequest(
      {
        hostname: url.hostname,
        port: url.port || 443,
        path: url.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/timestamp-query",
          "Content-Length": tsq.length,
          "User-Agent": "chain-of-consciousness-ts/1.0",
        },
        timeout,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const body = Buffer.concat(chunks);
          const status = parseTsrStatus(body);
          resolve({
            success: status.status === 0 || status.status === 1,
            anchor_type: "rfc3161",
            proof: body,
            timestamp: new Date().toISOString(),
          });
        });
      },
    );

    req.on("error", (err) => {
      resolve({
        success: false,
        anchor_type: "rfc3161",
        error: err.message,
      });
    });

    req.on("timeout", () => {
      req.destroy();
      resolve({
        success: false,
        anchor_type: "rfc3161",
        error: "Request timed out",
      });
    });

    req.write(tsq);
    req.end();
  });
}

export function submitOts(
  hashHex: string,
  calendars: string[] = DEFAULT_OTS_CALENDARS,
  timeout = 30000,
): Promise<AnchorResult> {
  const hashBytes = Buffer.from(hashHex, "hex");

  const promises = calendars.map(
    (serverUrl) =>
      new Promise<Buffer | null>((resolve) => {
        const url = new URL(`${serverUrl}/digest`);
        const req = httpsRequest(
          {
            hostname: url.hostname,
            port: url.port || 443,
            path: url.pathname,
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded",
              "Content-Length": hashBytes.length,
              "User-Agent": "chain-of-consciousness-ts/1.0",
              Accept: "application/vnd.opentimestamps.v1",
            },
            timeout,
          },
          (res) => {
            const chunks: Buffer[] = [];
            res.on("data", (chunk: Buffer) => chunks.push(chunk));
            res.on("end", () => {
              const body = Buffer.concat(chunks);
              resolve(body.length > 0 ? body : null);
            });
          },
        );

        req.on("error", () => resolve(null));
        req.on("timeout", () => {
          req.destroy();
          resolve(null);
        });

        req.write(hashBytes);
        req.end();
      }),
  );

  return Promise.all(promises).then((results) => {
    const proofs = results.filter(Boolean) as Buffer[];
    if (proofs.length === 0) {
      return {
        success: false,
        anchor_type: "opentimestamps",
        error: "No calendar servers responded",
      };
    }

    return {
      success: true,
      anchor_type: "opentimestamps",
      proof: proofs[0],
      timestamp: new Date().toISOString(),
    };
  });
}
