export const MAX_FILE_BYTES = 1_000_000;

export async function encodeFile(file) {
  if (!file || !/\.(csv|xlsx)$/i.test(file.name)) {
    throw new Error("Choose a CSV or XLSX file.");
  }
  if (!file.size || file.size > MAX_FILE_BYTES) {
    throw new Error("Choose a non-empty file of at most 1,000,000 bytes.");
  }
  if (file.name.length > 120 || /[/\\\x00-\x1f]/.test(file.name)) {
    throw new Error("Use a filename without path components, at most 120 characters.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return { filename: file.name, file_data: `data:application/octet-stream;base64,${btoa(binary)}` };
}
