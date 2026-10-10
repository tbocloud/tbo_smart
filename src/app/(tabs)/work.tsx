import { useLocalSearchParams, useRouter } from "expo-router";
import { FolderKanban, FileText, Inbox, ListTodo, Sparkles, X } from "@/components/icons";
import { View } from "react-native";

import { getMyTasks, getProjects, getTickets, taskGroup } from "@/api/hub";
import type { TaskGroup, TicketView } from "@/api/types";
import {
  Badge,
  Card,
  Chips,
  EmptyState,
  Pressable,
  QueryView,
  Screen,
  SectionHeader,
  SegmentedControl,
  Text,
  ToneIcon,
} from "@/components";
import { TaskCard } from "@/features/tasks/TaskCard";
import { TicketRow } from "@/features/tickets/TicketRow";
import { en } from "@/i18n/en";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

type Segment = "tasks" | "tickets" | "projects";
const GROUPS: TaskGroup[] = ["overdue", "today", "upcoming", "on_hold", "in_review"];

export default function Work() {
  const router = useRouter();
  // Segment, filter and view live in the URL so deep links and Home tiles land on the right list.
  const params = useLocalSearchParams<{ segment?: Segment; filter?: TaskGroup; view?: TicketView }>();
  const segment: Segment = params.segment ?? "tasks";

  return (
    <Screen title={en.work.title}>
      <SegmentedControl<Segment>
        label={en.work.title}
        value={segment}
        onChange={(s) => router.setParams({ segment: s, filter: undefined })}
        options={[
          { value: "tasks", label: en.work.segments.tasks },
          { value: "tickets", label: en.work.segments.tickets },
          { value: "projects", label: en.work.segments.projects },
        ]}
      />
      {segment === "tasks" ? <TasksList filter={params.filter} /> : null}
      {segment === "tickets" ? <TicketsList view={params.view ?? "mine"} /> : null}
      {segment === "projects" ? <ProjectsList /> : null}
    </Screen>
  );
}

function TasksList({ filter }: { filter?: TaskGroup }) {
  const router = useRouter();
  const { colors } = useTheme();
  const tasks = useQuery("hub:tasks", getMyTasks);
  return (
    <QueryView query={tasks}>
      {(list) => {
        if (list.length === 0) {
          return <EmptyState icon={ListTodo} title={en.work.tasksEmpty} body={en.work.tasksEmptyBody} />;
        }
        const groups = GROUPS.filter((g) => !filter || g === filter);
        return (
          <View style={{ gap: space.xl }}>
            {filter ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${en.work.filtered(en.work.groups[filter])}. ${en.work.clearFilter}`}
                onPress={() => router.setParams({ filter: undefined })}
                ringRadius={radius.pill}
                style={({ pressed }) => ({
                  alignSelf: "flex-start",
                  flexDirection: "row",
                  alignItems: "center",
                  gap: space.sm,
                  minHeight: 40,
                  paddingLeft: space.lg,
                  paddingRight: space.md,
                  borderRadius: radius.pill,
                  backgroundColor: colors.brandSoft,
                  opacity: pressed ? 0.8 : 1,
                })}
              >
                <Text variant="small" weight="medium" color="brandInk">
                  {en.work.filtered(en.work.groups[filter])}
                </Text>
                <X size={16} color={colors.brandInk} />
              </Pressable>
            ) : null}
            {groups.map((g) => {
              const items = list.filter((t) => taskGroup(t) === g);
              if (!items.length) return null;
              return (
                <View key={g} style={{ gap: space.md }}>
                  <SectionHeader title={en.work.groups[g]} count={items.length} />
                  {items.map((t) => (
                    <TaskCard key={t.name} task={t} onPress={() => router.push(`/task/${t.name}`)} />
                  ))}
                </View>
              );
            })}
          </View>
        );
      }}
    </QueryView>
  );
}

function TicketsList({ view }: { view: TicketView }) {
  const router = useRouter();
  const tickets = useQuery(`hub:tickets:${view}`, () => getTickets(view));
  return (
    <View style={{ gap: space.lg }}>
      <Chips<TicketView>
        label={en.a11y.ticketView}
        value={view}
        onChange={(v) => router.setParams({ view: v })}
        options={[
          { value: "mine", label: en.work.ticketViews.mine },
          { value: "unassigned", label: en.work.ticketViews.unassigned },
          { value: "team", label: en.work.ticketViews.team },
        ]}
      />
      <QueryView query={tickets}>
        {(list) =>
          list.length === 0 ? (
            <EmptyState icon={Inbox} title={en.work.ticketsEmpty[view]} body={en.work.ticketsEmptyBody} />
          ) : (
            <View style={{ gap: space.md }}>
              {list.map((t) => (
                <TicketRow key={t.name} ticket={t} onPress={() => router.push(`/ticket/${t.name}`)} />
              ))}
            </View>
          )
        }
      </QueryView>
    </View>
  );
}

function ProjectsList() {
  const router = useRouter();
  const projects = useQuery("hub:projects", getProjects);
  return (
    <QueryView query={projects}>
      {(list) =>
        list.length === 0 ? (
          <EmptyState icon={FolderKanban} title={en.work.projectsEmpty} body={en.work.projectsEmptyBody} />
        ) : (
          <View style={{ gap: space.md }}>
            {list.map((p) => (
              <Card
                key={p.name}
                onPress={() => router.push(`/project/${p.name}`)}
                accessibilityLabel={`${p.project_name}, ${p.customer}, ${en.work.openTasks(p.open_tasks)}, ${en.work.files(p.files)}${p.for_you ? `, ${en.work.forYou(p.for_you)}` : ""}`}
              >
                <View style={{ flexDirection: "row", gap: space.md }}>
                  <ToneIcon icon={FolderKanban} size={40} />
                  <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                    <Text variant="small" color="ink5" numberOfLines={1}>
                      <Text variant="small" mono color="ink5">
                        {p.name}
                      </Text>
                      {` · ${p.department}`}
                    </Text>
                    <Text variant="title" weight="semibold" numberOfLines={1}>
                      {p.project_name}
                    </Text>
                    <Text variant="small" color="ink6" numberOfLines={1}>
                      {p.customer}
                    </Text>
                    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm, marginTop: space.sm }}>
                      <Badge label={en.work.openTasks(p.open_tasks)} icon={ListTodo} outline />
                      <Badge label={en.work.files(p.files)} icon={FileText} outline />
                      {p.for_you ? <Badge label={en.work.forYou(p.for_you)} icon={Sparkles} /> : null}
                    </View>
                  </View>
                </View>
              </Card>
            ))}
          </View>
        )
      }
    </QueryView>
  );
}
