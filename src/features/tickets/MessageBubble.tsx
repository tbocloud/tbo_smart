import { Lock, Paperclip } from "@/components/icons";
import { View } from "react-native";

import type { TicketMessage } from "@/api/types";
import { Avatar, Text } from "@/components";
import { en } from "@/i18n/en";
import { dayLabel, time } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

/**
 * Customer and agent messages share one neutral style (the name says who);
 * internal notes use the pale "note" style with a lock, as on the web.
 */
export function MessageBubble({ message, mine }: { message: TicketMessage; mine: boolean }) {
  const { colors } = useTheme();
  const note = message.kind === "note";
  return (
    <View style={{ flexDirection: "row", gap: space.md }}>
      <Avatar name={message.author} size={32} />
      <View style={{ flex: 1, minWidth: 0, gap: space.xs }}>
        <View style={{ flexDirection: "row", alignItems: "baseline", gap: space.sm, flexWrap: "wrap" }}>
          <Text variant="body" weight="semibold">
            {mine ? en.ticket.you : message.author}
          </Text>
          <Text variant="caption" mono color="ink5">
            {dayLabel(message.at)} · {time(message.at)}
          </Text>
        </View>
        <View
          style={{
            padding: space.md,
            borderRadius: radius.lg,
            borderTopLeftRadius: radius.sm,
            backgroundColor: note ? colors.note : message.kind === "agent" ? colors.surfaceSunken : colors.surface,
            borderWidth: 1,
            borderColor: note ? colors.noteBorder : colors.outline2,
            gap: space.sm,
          }}
        >
          {note ? (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <Lock size={13} color={colors.ink6} />
              <Text variant="caption" weight="medium" color="ink6">
                {en.ticket.note}
              </Text>
            </View>
          ) : null}
          <Text variant="body" color="ink7" selectable>
            {message.body}
          </Text>
          {message.attachments?.map((a) => (
            <View
              key={a.name}
              style={{
                flexDirection: "row",
                alignItems: "center",
                gap: space.sm,
                paddingVertical: space.sm,
                paddingHorizontal: space.md,
                borderRadius: radius.md,
                borderWidth: 1,
                borderColor: colors.outline2,
                backgroundColor: colors.surface,
              }}
            >
              <Paperclip size={14} color={colors.ink5} />
              <Text variant="small" numberOfLines={1} style={{ flex: 1 }}>
                {a.name}
              </Text>
              <Text variant="caption" mono color="ink5">
                {a.size}
              </Text>
            </View>
          ))}
        </View>
      </View>
    </View>
  );
}
