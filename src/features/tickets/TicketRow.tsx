import { AlarmClock, Hourglass } from "@/components/icons";
import { View } from "react-native";

import type { Ticket, TicketPriority } from "@/api/types";
import { Badge, Card, StatusPill, Text } from "@/components";
import { en } from "@/i18n/en";
import { slaInfo } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";
import { space, type Tone } from "@/theme/tokens";

const PRIORITY_TONE: Record<TicketPriority, Tone> = {
  Urgent: "danger",
  High: "warning",
  Medium: "neutral",
  Low: "neutral",
};

export function SlaBadge({ due }: { due: string }) {
  const sla = slaInfo(due);
  return <Badge label={sla.text} tone={sla.tone} icon={sla.overdue ? AlarmClock : Hourglass} />;
}

export function PriorityLabel({ priority }: { priority: TicketPriority }) {
  const tone = PRIORITY_TONE[priority];
  return <Badge label={priority} tone={tone} outline={tone === "neutral"} />;
}

export function TicketRow({ ticket, onPress }: { ticket: Ticket; onPress: () => void }) {
  const { colors } = useTheme();
  const sla = slaInfo(ticket.sla_due);
  return (
    <Card
      onPress={onPress}
      accent={sla.overdue ? colors.danger : undefined}
      accessibilityLabel={en.a11y.ticket(ticket.name, ticket.subject, ticket.customer, ticket.priority, ticket.status, sla.text)}
    >
      <View style={{ gap: space.sm }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
          <Text variant="small" mono color="ink5">
            #{ticket.name}
          </Text>
          <Text variant="small" color="ink5" numberOfLines={1} style={{ flex: 1 }}>
            {ticket.customer}
          </Text>
        </View>
        <Text variant="title" weight="semibold" numberOfLines={2}>
          {ticket.subject}
        </Text>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
          <SlaBadge due={ticket.sla_due} />
          <StatusPill status={ticket.status} />
          <PriorityLabel priority={ticket.priority} />
        </View>
      </View>
    </Card>
  );
}
