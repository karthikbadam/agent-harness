import { useQuery } from "@tanstack/react-query";
import { Box, Image, Link, Spinner, Stack, Table, Text } from "@chakra-ui/react";

import { tasksApi } from "../api/tasks";
import { MarkdownText } from "./MarkdownText";
import type { ArtifactOut } from "../types";

/**
 * Renders one artifact by its `kind`, so a loop (or any task) shows whatever it
 * produced — not just graphs:
 *   - graph  → inline image
 *   - table  → parsed TSV/CSV rendered as a real table
 *   - report → markdown/text
 *   - log    → monospace text (tail)
 *   - file   → download link
 * Anything unknown falls back to a download link.
 */
export function ArtifactView({
  artifact,
  live = false,
}: {
  artifact: ArtifactOut;
  live?: boolean;
}) {
  return (
    <Stack gap={1.5}>
      <Text fontSize="2xs" color="fg.subtle" textTransform="uppercase">
        {artifact.kind} · {artifact.name}
      </Text>
      <ArtifactBody artifact={artifact} live={live} />
    </Stack>
  );
}

function ArtifactBody({ artifact, live }: { artifact: ArtifactOut; live: boolean }) {
  switch (artifact.kind) {
    case "graph":
      return (
        <Image
          src={tasksApi.artifactUrl(artifact.id)}
          alt={artifact.name}
          w="100%"
          rounded="md"
          borderWidth="1px"
          borderColor="border"
        />
      );
    case "table":
      return <TableArtifact artifact={artifact} live={live} />;
    case "report":
      return <TextArtifact artifact={artifact} live={live} render="markdown" />;
    case "log":
      return <TextArtifact artifact={artifact} live={live} render="pre" tail />;
    default:
      return <DownloadLink artifact={artifact} />;
  }
}

function DownloadLink({ artifact }: { artifact: ArtifactOut }) {
  return (
    <Link
      href={tasksApi.artifactUrl(artifact.id)}
      fontSize="sm"
      color="blue.fg"
      borderWidth="1px"
      borderColor="border"
      rounded="md"
      px={3}
      py={1.5}
      w="fit-content"
    >
      ↓ {artifact.name}
    </Link>
  );
}

/** Fetch an artifact's bytes as text. The download URL carries the token. */
function useArtifactText(artifact: ArtifactOut, live: boolean) {
  return useQuery({
    queryKey: ["artifact-text", artifact.id],
    queryFn: () => fetch(tasksApi.artifactUrl(artifact.id)).then((r) => r.text()),
    refetchInterval: live ? 5_000 : false,
  });
}

function parseDelimited(text: string): { header: string[]; rows: string[][] } | null {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  const first = lines[0];
  if (!first) return null;
  const delim = first.includes("\t") ? "\t" : ",";
  const split = (l: string) => l.split(delim);
  return { header: split(first), rows: lines.slice(1).map(split) };
}

/** Agents register "table" artifacts as either real TSV/CSV or as a markdown
 * doc with a pipe-table. Detect markdown so we render it properly instead of
 * comma-splitting prose. */
function looksMarkdown(text: string): boolean {
  if (/^\s*#{1,6}\s/.test(text)) return true; // a heading
  if (/\|[\s:-]*-{2,}[\s:-]*\|/.test(text)) return true; // a |---|---| separator
  return false;
}

function TableArtifact({ artifact, live }: { artifact: ArtifactOut; live: boolean }) {
  const { data, isLoading } = useArtifactText(artifact, live);
  if (isLoading) return <Spinner size="sm" />;
  if (data == null) return <DownloadLink artifact={artifact} />;
  // Markdown ledgers (headings / pipe-tables) render as markdown — MarkdownText
  // turns the pipe-table into a real table and keeps the surrounding prose.
  if (looksMarkdown(data)) {
    return (
      <Box maxH="26rem" overflowY="auto">
        <MarkdownText source={data} />
      </Box>
    );
  }
  const parsed = parseDelimited(data);
  if (!parsed) return <DownloadLink artifact={artifact} />;
  return (
    <Box overflowX="auto" maxH="22rem" overflowY="auto" borderWidth="1px" borderColor="border" rounded="md">
      <Table.Root size="sm" variant="line" stickyHeader>
        <Table.Header>
          <Table.Row>
            {parsed.header.map((h, i) => (
              <Table.ColumnHeader key={i} fontWeight="semibold" fontSize="2xs">
                {h}
              </Table.ColumnHeader>
            ))}
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {parsed.rows.map((row, ri) => (
            <Table.Row key={ri}>
              {row.map((cell, ci) => (
                <Table.Cell key={ci} fontSize="2xs" fontFamily={ci === 0 ? "mono" : undefined}>
                  {cell}
                </Table.Cell>
              ))}
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </Box>
  );
}

function TextArtifact({
  artifact,
  live,
  render,
  tail = false,
}: {
  artifact: ArtifactOut;
  live: boolean;
  render: "markdown" | "pre";
  tail?: boolean;
}) {
  const { data, isLoading } = useArtifactText(artifact, live);
  if (isLoading) return <Spinner size="sm" />;
  if (data == null) return <DownloadLink artifact={artifact} />;
  const text = tail ? data.split(/\r?\n/).slice(-200).join("\n") : data;
  if (render === "markdown") return <MarkdownText source={text} />;
  return (
    <Box
      as="pre"
      fontFamily="mono"
      fontSize="2xs"
      whiteSpace="pre-wrap"
      bg="bg.muted"
      rounded="md"
      p={3}
      maxH="22rem"
      overflowY="auto"
    >
      {text}
    </Box>
  );
}
