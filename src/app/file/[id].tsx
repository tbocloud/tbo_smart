import * as Clipboard from "expo-clipboard";
import { useLocalSearchParams } from "expo-router";
import { Copy, Download, FileText } from "@/components/icons";
import { View } from "react-native";

import { getFile } from "@/api/hub";
import type { ProjectFile } from "@/api/types";
import { Badge, Button, Card, EmptyState, QueryView, Screen, Text, useToast } from "@/components";
import { Markdown } from "@/features/files/Markdown";
import { en } from "@/i18n/en";
import { dayLabel } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

export default function FileViewer() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const toast = useToast();
  const file = useQuery(`hub:file:${id}`, () => getFile(id));
  const data = file.data;

  const footer = data ? (
    data.file.kind === "md" && data.text ? (
      <Button
        label={en.common.copy}
        icon={Copy}
        variant="primary"
        size="lg"
        full
        onPress={() => {
          void Clipboard.setStringAsync(data.text ?? "").then(() => toast(en.common.copied, "success"));
        }}
      />
    ) : (
      <Button label={en.projects.download} icon={Download} variant="primary" size="lg" full onPress={() => toast(en.projects.downloadNote)} />
    )
  ) : undefined;

  return (
    <Screen back title={data?.file.file_name ?? ""} subtitle={data ? meta(data.file) : undefined} footer={footer}>
      <QueryView query={file}>
        {(d) => {
          if (!d) return <EmptyState icon={FileText} title={en.states.notFound} />;
          if (d.file.kind === "md" && d.text) {
            return (
              <Card>
                <Markdown source={d.text} />
              </Card>
            );
          }
          return <PdfPlaceholder file={d.file} />;
        }}
      </QueryView>
    </Screen>
  );
}

function meta(f: ProjectFile): string {
  return [f.size, f.uploaded_by, dayLabel(f.modified)].join(" · ");
}

/** Page frame standing in for the native PDF viewer (react-native-pdf in the app build). */
function PdfPlaceholder({ file }: { file: ProjectFile }) {
  const { colors } = useTheme();
  const bar = (w: `${number}%`, h = 8) => (
    <View style={{ width: w, height: h, borderRadius: 2, backgroundColor: colors.surfaceRaised }} />
  );
  return (
    <View style={{ gap: space.lg }}>
      <View
        accessibilityLabel={`${en.projects.pdfTitle}: ${file.file_name}`}
        style={{
          aspectRatio: 1 / 1.414,
          backgroundColor: colors.surface,
          borderRadius: radius.md,
          borderWidth: 1,
          borderColor: colors.outline2,
          padding: space.xl,
          gap: space.md,
        }}
      >
        <Text variant="title" weight="semibold">
          {file.file_name.replace(/\.pdf$/, "")}
        </Text>
        {bar("40%", 6)}
        <View style={{ height: space.md }} />
        {bar("100%")}
        {bar("94%")}
        {bar("97%")}
        {bar("60%")}
        <View style={{ height: space.sm }} />
        {bar("100%")}
        {bar("88%")}
        {bar("92%")}
        {bar("45%")}
      </View>
      <View style={{ flexDirection: "row", gap: space.sm, alignItems: "flex-start" }}>
        <Badge label={en.projects.pdfTitle} icon={FileText} />
        <Text variant="small" color="ink5" style={{ flex: 1 }}>
          {en.projects.pdfBody}
        </Text>
      </View>
    </View>
  );
}
