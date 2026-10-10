import { useLocalSearchParams } from "expo-router";
import { ChevronDown, Inbox, Lock, Paperclip, Send, X } from "@/components/icons";
import { useState } from "react";
import { TextInput, View } from "react-native";

import { getMe, getTicket, replyTicket, setTicketStatus } from "@/api/hub";
import type { Ticket, TicketStatus } from "@/api/types";
import {
  Button,
  EmptyState,
  IconButton,
  OptionList,
  Pressable,
  QueryView,
  Screen,
  Sheet,
  StatusPill,
  Text,
  Toggle,
  useToast,
} from "@/components";
import { MessageBubble } from "@/features/tickets/MessageBubble";
import { PriorityLabel, SlaBadge } from "@/features/tickets/TicketRow";
import { en } from "@/i18n/en";
import { writeErrorMessage } from "@/lib/errors";
import { useMutation, useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { fonts, radius, space, type } from "@/theme/tokens";

const STATUSES: TicketStatus[] = ["Open", "Replied", "Paused", "Resolved", "Closed"];

export default function TicketDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const ticket = useQuery(`hub:ticket:${id}`, () => getTicket(id));
  return (
    <Screen
      back
      title={en.ticket.title(id)}
      subtitle={ticket.data?.customer}
      onRefresh={ticket.refresh}
      refreshing={ticket.refreshing}
      footer={ticket.data ? <Composer ticket={ticket.data} /> : undefined}
    >
      <QueryView query={ticket}>
        {(t) => (t ? <TicketBody ticket={t} /> : <EmptyState icon={Inbox} title={en.states.notFound} />)}
      </QueryView>
    </Screen>
  );
}

function TicketBody({ ticket }: { ticket: Ticket }) {
  const { colors } = useTheme();
  const toast = useToast();
  const [picking, setPicking] = useState(false);
  const me = useQuery("hub:me", getMe);
  const status = useMutation((s: TicketStatus) => setTicketStatus(ticket.name, s).then(() => s), {
    invalidates: [`hub:ticket:${ticket.name}`, "hub:tickets", "hub:home"],
    onSuccess: (s) => {
      setPicking(false);
      toast(en.ticket.statusChanged(s), "success");
    },
    onError: (e) => toast(writeErrorMessage(e), "danger"),
  });

  return (
    <>
      <View style={{ gap: space.sm }}>
        <Text variant="h2" weight="semibold" accessibilityRole="header">
          {ticket.subject}
        </Text>
        <Text variant="small" color="ink6">
          {ticket.contact} · {ticket.customer}
        </Text>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm, alignItems: "center", marginTop: space.xs }}>
          <SlaBadge due={ticket.sla_due} />
          <PriorityLabel priority={ticket.priority} />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`${en.ticket.status}: ${ticket.status}. ${en.ticket.changeStatus}`}
            onPress={() => setPicking(true)}
            ringRadius={radius.sm}
            hitSlop={{ top: 12, bottom: 12 }}
            style={({ pressed }) => ({
              flexDirection: "row",
              alignItems: "center",
              gap: 2,
              borderRadius: radius.sm,
              opacity: pressed ? 0.7 : 1,
            })}
          >
            <StatusPill status={ticket.status} />
            <ChevronDown size={16} color={colors.ink5} />
          </Pressable>
        </View>
      </View>

      <View style={{ gap: space.xl }}>
        {ticket.conversation.map((m) => (
          <MessageBubble key={m.id} message={m} mine={m.author === me.data?.full_name} />
        ))}
      </View>

      <Sheet open={picking} onClose={() => setPicking(false)} title={en.ticket.changeStatus} subtitle={`#${ticket.name}`}>
        <OptionList
          options={STATUSES.map((s) => ({ value: s, label: s }))}
          value={ticket.status}
          onChange={(s) => {
            if (s !== ticket.status) void status.mutate(s);
            else setPicking(false);
          }}
        />
      </Sheet>
    </>
  );
}

function Composer({ ticket }: { ticket: Ticket }) {
  const { colors } = useTheme();
  const toast = useToast();
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(false);
  const [files, setFiles] = useState<string[]>([]);
  const send = useMutation(replyTicket, {
    invalidates: [`hub:ticket:${ticket.name}`, "hub:tickets", "hub:home"],
    onSuccess: () => {
      toast(internal ? en.ticket.noted : en.ticket.sent, "success");
      setBody("");
      setFiles([]);
    },
    onError: (e) => toast(writeErrorMessage(e), "danger"),
  });

  return (
    <View style={{ gap: space.sm }}>
      <View
        style={{
          borderRadius: radius.lg,
          borderWidth: 1,
          borderColor: internal ? colors.noteBorder : colors.outline3,
          backgroundColor: internal ? colors.note : colors.surface,
          paddingHorizontal: space.md,
          paddingTop: space.sm,
        }}
      >
        <TextInput
          value={body}
          onChangeText={setBody}
          placeholder={internal ? en.ticket.notePlaceholder : en.ticket.replyPlaceholder}
          placeholderTextColor={colors.ink4}
          multiline
          accessibilityLabel={internal ? en.ticket.internalNote : en.ticket.replyPlaceholder}
          style={{
            ...type.body,
            fontFamily: fonts.regular,
            color: colors.ink9,
            minHeight: 44,
            maxHeight: 120,
            textAlignVertical: "top",
            outlineStyle: "none" as never,
          }}
        />
        {files.length ? (
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm, paddingBottom: space.sm }}>
            {files.map((f) => (
              <View
                key={f}
                style={{
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 4,
                  paddingLeft: space.sm,
                  borderRadius: radius.sm,
                  backgroundColor: colors.surfaceSunken,
                }}
              >
                <Paperclip size={12} color={colors.ink5} />
                <Text variant="caption">{f}</Text>
                <IconButton icon={X} label={en.a11y.remove(f)} onPress={() => setFiles([])} />
              </View>
            ))}
          </View>
        ) : null}
      </View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
        <IconButton
          icon={Paperclip}
          label={en.ticket.attach}
          onPress={() => {
            setFiles(["screenshot.png"]);
            toast(en.ticket.attached);
          }}
        />
        <Pressable
          accessibilityRole="switch"
          accessibilityState={{ checked: internal }}
          accessibilityLabel={en.ticket.internalNote}
          onPress={() => setInternal((v) => !v)}
          style={{ flexDirection: "row", alignItems: "center", gap: space.sm, minHeight: 48, flex: 1 }}
        >
          <Toggle value={internal} onChange={setInternal} />
          <Lock size={14} color={colors.ink6} />
          <Text variant="small" weight="medium" color="ink7">
            {en.ticket.internalNote}
          </Text>
        </Pressable>
        <Button
          label={internal ? en.ticket.addNote : en.ticket.send}
          icon={Send}
          variant="primary"
          disabled={!body.trim()}
          loading={send.pending}
          onPress={() => void send.mutate(ticket.name, body.trim(), internal, files)}
        />
      </View>
    </View>
  );
}
