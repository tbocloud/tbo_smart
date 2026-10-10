import { getNotifications as getEssNotifications } from "@/api/ess";
import { getHome } from "@/api/hub";
import { useQuery } from "@/lib/query";

/** Unread alerts across both sites (hub count from get_home, HR from its list). */
export function useUnreadCount(): number {
  const home = useQuery("hub:home", getHome);
  const ess = useQuery("ess:notifications", getEssNotifications);
  return (home.data?.unread_notifications ?? 0) + (ess.data?.filter((n) => !n.read).length ?? 0);
}
