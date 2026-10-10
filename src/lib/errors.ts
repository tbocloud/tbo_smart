import type { ApiError } from "@/api/client";
import { en } from "@/i18n/en";

/** Friendly message for a failed write; says what happened and what to do. */
export function writeErrorMessage(e: ApiError): string {
  if (e.code === "offline") return en.common.offlineWrite;
  if (e.code === "reconnect") return en.common.reconnectWrite(e.site === "hub" ? en.sites.hub : en.sites.ess);
  return en.common.savingFailed;
}
