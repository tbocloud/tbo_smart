import { Fragment, type ReactNode } from "react";
import { ScrollView, View } from "react-native";

import { Text } from "@/components";
import { useTheme } from "@/theme/ThemeProvider";
import { fonts, radius, space } from "@/theme/tokens";

/**
 * Small Markdown renderer for project prompts: headings, paragraphs, bullet
 * and numbered lists, fenced code, **bold**, *italic* and `code`. Enough for
 * master prompts without pulling in a Markdown dependency; swap for
 * react-native-markdown-display if files start using tables or images.
 */

type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "p"; text: string }
  | { kind: "li"; ordered: boolean; marker: string; text: string }
  | { kind: "code"; text: string };

function parseBlocks(source: string): Block[] {
  const lines = source.split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: "p", text: para.join(" ") });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      blocks.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*(\d+)\.\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "h", level: heading[1].length, text: heading[2] });
    } else if (bullet) {
      flush();
      blocks.push({ kind: "li", ordered: false, marker: "•", text: bullet[1] });
    } else if (numbered) {
      flush();
      blocks.push({ kind: "li", ordered: true, marker: `${numbered[1]}.`, text: numbered[2] });
    } else if (line.trim() === "") {
      flush();
    } else {
      para.push(line.trim());
    }
  }
  flush();
  return blocks;
}

function Inline({ text }: { text: string }) {
  const { colors } = useTheme();
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g).filter(Boolean);
  const out: ReactNode[] = parts.map((part, i) => {
    if (part.startsWith("**")) {
      return (
        <Text key={i} weight="semibold" color="ink9">
          {part.slice(2, -2)}
        </Text>
      );
    }
    if (part.startsWith("`")) {
      return (
        <Text key={i} mono variant="small" style={{ backgroundColor: colors.surfaceRaised }}>
          {` ${part.slice(1, -1)} `}
        </Text>
      );
    }
    if (part.startsWith("*")) {
      return (
        <Text key={i} color="ink7" style={{ fontStyle: "italic" }}>
          {part.slice(1, -1)}
        </Text>
      );
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
  return <>{out}</>;
}

export function Markdown({ source }: { source: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: space.md }}>
      {parseBlocks(source).map((b, i) => {
        if (b.kind === "h") {
          return (
            <Text
              key={i}
              variant={b.level === 1 ? "h2" : "title"}
              weight="semibold"
              accessibilityRole="header"
              style={{ marginTop: i === 0 ? 0 : space.sm }}
            >
              {b.text}
            </Text>
          );
        }
        if (b.kind === "li") {
          return (
            <View key={i} style={{ flexDirection: "row", gap: space.sm, paddingLeft: space.xs }}>
              <Text color="ink5" mono={b.ordered} style={{ minWidth: 16 }}>
                {b.marker}
              </Text>
              <Text color="ink7" style={{ flex: 1 }} selectable>
                <Inline text={b.text} />
              </Text>
            </View>
          );
        }
        if (b.kind === "code") {
          return (
            <View
              key={i}
              style={{
                backgroundColor: colors.surfaceSunken,
                borderRadius: radius.md,
                borderWidth: 1,
                borderColor: colors.outline2,
                padding: space.md,
              }}
            >
              <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                <Text selectable style={{ fontFamily: fonts.mono, fontSize: 13, lineHeight: 20 }} color="ink7">
                  {b.text}
                </Text>
              </ScrollView>
            </View>
          );
        }
        return (
          <Text key={i} color="ink7" selectable>
            <Inline text={b.text} />
          </Text>
        );
      })}
    </View>
  );
}
