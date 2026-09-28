import { homedir } from "node:os";
import { join } from "node:path";

export function productDataRoot(): string {
  return join(process.env.LOCALAPPDATA ?? join(homedir(), "AppData", "Local"), "Nirai-v2");
}
