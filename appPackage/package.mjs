import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const directory = dirname(fileURLToPath(import.meta.url));

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, bytes) {
  const name = Buffer.from(type);
  const size = Buffer.alloc(4);
  size.writeUInt32BE(bytes.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([name, bytes])));
  return Buffer.concat([size, name, bytes, crc]);
}
function icon(size, outline) {
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const u = x / size, v = y / size;
    const bars = (u > .22 && u < .36 && v > .48 && v < .76)
      || (u > .43 && u < .57 && v > .26 && v < .76)
      || (u > .64 && u < .78 && v > .38 && v < .76);
    const color = bars ? [255, 255, 255, 255] : outline ? [0, 0, 0, 0] : [116, 83, 167, 255];
    raw.set(color, y * (size * 4 + 1) + 1 + x * 4);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

writeFileSync(join(directory, "color.png"), icon(192, false));
writeFileSync(join(directory, "outline.png"), icon(32, true));
if (process.argv.includes("--icons-only")) {
  console.log("Generated original chart icons.");
} else {
  const required = ["COPILOT_APP_ID", "PUBLISHER_NAME", "WEBSITE_URL", "PRIVACY_URL", "TERMS_URL", "GATEWAY_PUBLIC_ORIGIN", "COPILOT_OAUTH_REFERENCE_ID"];
  for (const name of required) if (!process.env[name]) throw new Error(`Set ${name} before packaging. No resources will be provisioned.`);
  const origin = new URL(process.env.GATEWAY_PUBLIC_ORIGIN);
  if (origin.protocol !== "https:" || origin.pathname !== "/" || origin.search || origin.hash || origin.username || origin.password) throw new Error("GATEWAY_PUBLIC_ORIGIN must be an HTTPS origin.");
  if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(process.env.COPILOT_APP_ID)) throw new Error("COPILOT_APP_ID must be the real registered app UUID.");
  const values = { ...process.env, GATEWAY_HOSTNAME: origin.hostname, GATEWAY_PUBLIC_ORIGIN: origin.origin };
  function resolve(value) {
    if (typeof value === "string") return value.replace(/\$\{\{([A-Z0-9_]+)\}\}/g, (_match, name) => {
      if (!values[name]) throw new Error(`Missing ${name}`);
      return values[name];
    });
    if (Array.isArray(value)) return value.map(resolve);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolve(item)]));
    return value;
  }
  const output = join(directory, "build");
  mkdirSync(output, { recursive: true });
  for (const file of ["manifest.json", "declarativeAgent.json", "ai-plugin.json", "mcp-tools.json"]) {
    writeFileSync(join(output, file), `${JSON.stringify(resolve(JSON.parse(readFileSync(join(directory, file), "utf8"))), null, 2)}\n`);
  }
  for (const file of ["color.png", "outline.png"]) copyFileSync(join(directory, file), join(output, file));
  console.log(`Resolved package written to ${output}. Validate it with Agents Toolkit before uploading.`);
}
