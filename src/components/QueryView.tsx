import { CloudOff, Lock, Unplug } from "@/components/icons";
import type { ReactNode } from "react";

import { en } from "@/i18n/en";
import type { Query } from "@/lib/query";
import { useSession } from "@/lib/session";

import { EmptyState } from "./EmptyState";
import { SkeletonCards } from "./Skeleton";

type Props<T> = {
  query: Query<T>;
  children: (data: T) => ReactNode;
  skeleton?: ReactNode;
  compact?: boolean;
};

/**
 * Loading → skeleton, failure → a way forward, success → content. Cached data
 * stays on screen when a refresh fails (the offline banner explains why).
 */
export function QueryView<T>({ query, children, skeleton, compact }: Props<T>) {
  const { connect } = useSession();
  if (query.data !== undefined) return <>{children(query.data)}</>;
  if (query.loading) return <>{skeleton ?? <SkeletonCards />}</>;
  const error = query.error;
  if (error?.code === "reconnect") {
    const site = error.site === "hub" ? en.sites.hub : en.sites.ess;
    return (
      <EmptyState
        compact={compact}
        icon={Unplug}
        title={en.states.reconnectTitle(site)}
        body={en.states.reconnectBody(site)}
        action={{ label: `${en.common.reconnect} ${site}`, onPress: () => connect(error.site), variant: "primary" }}
      />
    );
  }
  if (error?.code === "forbidden") {
    return <EmptyState compact={compact} icon={Lock} title={en.states.forbiddenTitle} body={en.states.forbiddenBody} />;
  }
  return (
    <EmptyState
      compact={compact}
      icon={CloudOff}
      tone="danger"
      title={en.states.errorTitle}
      body={en.states.errorBody}
      action={{ label: en.common.retry, onPress: query.retry }}
    />
  );
}
