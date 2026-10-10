import { useLocalSearchParams, useRouter } from "expo-router";
import { ChevronRight, FileText, Folder, FolderOpen, Sparkles } from "@/components/icons";
import { ScrollView, View } from "react-native";

import { getProject, getProjectFiles } from "@/api/hub";
import type { ProjectFile } from "@/api/types";
import {
  Badge,
  Chips,
  EmptyState,
  Group,
  ListRow,
  Pressable,
  QueryView,
  Screen,
  SectionHeader,
  Text,
  ToneIcon,
} from "@/components";
import { en } from "@/i18n/en";
import { dayLabel } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space, type Tone } from "@/theme/tokens";

type Filter = "all" | "for_you";

const STATUS_TONE: Record<NonNullable<ProjectFile["status"]>, Tone> = {
  Final: "success",
  Draft: "neutral",
  "Needs review": "warning",
};

export default function ProjectScreen() {
  const router = useRouter();
  const { id, folder = "", filter = "all" } = useLocalSearchParams<{ id: string; folder?: string; filter?: Filter }>();
  const project = useQuery(`hub:project:${id}`, () => getProject(id));
  const files = useQuery(`hub:files:${id}`, () => getProjectFiles(id));

  return (
    <Screen back title={project.data?.project_name ?? en.projects.files} subtitle={project.data?.customer}>
      <Chips<Filter>
        label={en.projects.files}
        value={filter}
        onChange={(f) => router.setParams({ filter: f, folder: "" })}
        options={[
          { value: "all", label: en.projects.all },
          { value: "for_you", label: en.projects.forYou, count: files.data?.filter((f) => f.for_you).length },
        ]}
      />
      <QueryView query={files}>
        {(all) => {
          if (filter === "for_you") {
            const mine = all.filter((f) => f.for_you);
            return mine.length ? (
              <Group>
                {mine.map((f, i) => (
                  <FileRow key={f.name} file={f} showFolder last={i === mine.length - 1} />
                ))}
              </Group>
            ) : (
              <EmptyState icon={Sparkles} title={en.projects.empty} body={en.projects.emptyForYou} />
            );
          }
          const prefix = folder ? `${folder}/` : "";
          const here = all.filter((f) => f.folder === folder);
          const children = new Map<string, number>();
          for (const f of all) {
            if (folder && !f.folder.startsWith(prefix)) continue;
            if (f.folder === folder) continue;
            const rest = f.folder.slice(prefix.length);
            const name = rest.split("/")[0];
            if (name) children.set(name, (children.get(name) ?? 0) + 1);
          }
          if (!here.length && !children.size) {
            return <EmptyState icon={FolderOpen} title={en.projects.empty} body={en.projects.emptyBody} />;
          }
          return (
            <View style={{ gap: space.md }}>
              <Breadcrumbs folder={folder} onOpen={(path) => router.setParams({ folder: path })} />
              <Group>
                {[...children.entries()].map(([name, count], i) => (
                  <ListRow
                    key={name}
                    title={name}
                    subtitle={en.projects.folderFiles(count)}
                    leading={<ToneIcon icon={Folder} size={36} />}
                    onPress={() => router.setParams({ folder: prefix + name })}
                    divider={i < children.size - 1 || here.length > 0}
                  />
                ))}
                {here.map((f, i) => (
                  <FileRow key={f.name} file={f} last={i === here.length - 1} />
                ))}
              </Group>
            </View>
          );
        }}
      </QueryView>
    </Screen>
  );
}

function Breadcrumbs({ folder, onOpen }: { folder: string; onOpen: (path: string) => void }) {
  const { colors } = useTheme();
  const parts = folder ? folder.split("/") : [];
  const crumbs = [{ label: en.projects.root, path: "" }, ...parts.map((p, i) => ({ label: p, path: parts.slice(0, i + 1).join("/") }))];
  if (crumbs.length === 1) return <SectionHeader title={en.projects.root} />;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ alignItems: "center" }}>
      {crumbs.map((c, i) => {
        const last = i === crumbs.length - 1;
        return (
          <View key={c.path} style={{ flexDirection: "row", alignItems: "center" }}>
            {i > 0 ? <ChevronRight size={14} color={colors.ink4} /> : null}
            <Pressable
              accessibilityRole="link"
              accessibilityLabel={c.label}
              accessibilityState={{ selected: last }}
              disabled={last}
              onPress={() => onOpen(c.path)}
              style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: space.xs }}
            >
              <Text variant="body" weight={last ? "semibold" : "regular"} color={last ? "ink9" : "brandInk"}>
                {c.label}
              </Text>
            </Pressable>
          </View>
        );
      })}
    </ScrollView>
  );
}

function FileRow({ file, last, showFolder }: { file: ProjectFile; last: boolean; showFolder?: boolean }) {
  const router = useRouter();
  const meta = [file.size, en.projects.uploadedBy(file.uploaded_by), dayLabel(file.modified)];
  if (showFolder && file.folder) meta.unshift(file.folder);
  return (
    <ListRow
      title={file.file_name}
      subtitle={meta.join(" · ")}
      leading={<ToneIcon icon={FileText} size={36} />}
      onPress={() => router.push(`/file/${file.name}`)}
      divider={!last}
      trailing={
        <View style={{ alignItems: "flex-end", gap: 4 }}>
          {file.for_you ? <Badge label={en.projects.forYou} icon={Sparkles} /> : null}
          {file.status ? <Badge label={en.projects.statuses[file.status]} tone={STATUS_TONE[file.status]} /> : null}
        </View>
      }
    />
  );
}
