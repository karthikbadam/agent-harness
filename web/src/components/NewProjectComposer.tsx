import { Box, Text } from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";

import { Composer } from "./Composer";
import { useCreateProject } from "../hooks/useProjects";

/**
 * Parse a free-text project description into `{ name, path }`.
 *
 * Heuristic:
 *  1. Find the first path-like token (starts with `/`, `~/`, or `./`).
 *  2. The rest of the input (minus filler words like "at", "in", commas) is
 *     the name. If empty, fall back to the path's basename.
 *
 * Examples:
 *   "/Users/me/code/blog"                  → { name: "blog", path: "/Users/me/code/blog" }
 *   "a photo gallery at ~/code/gallery"    → { name: "a photo gallery", path: "~/code/gallery" }
 *   "blog, /Users/me/code/blog"            → { name: "blog", path: "/Users/me/code/blog" }
 */
export function parseProjectInput(
  text: string,
): { name: string; path: string } | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const pathRe = /(^|\s)(~\/[^\s]+|\/[^\s]+|\.\/[^\s]+)/;
  const match = pathRe.exec(trimmed);
  if (!match || !match[2]) return null;
  const path: string = match[2];
  const before = trimmed.slice(0, match.index).trim();
  const after = trimmed.slice(match.index + match[0].length).trim();
  const rawName = (before + " " + after).trim();
  const cleaned = rawName
    .replace(/\b(at|in|under|inside|the)\b/gi, " ")
    .replace(/[,;:]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const basename = path.replace(/\/+$/, "").split("/").pop() ?? path;
  const name: string = cleaned || basename;
  return { name, path };
}

export function NewProjectComposer() {
  const navigate = useNavigate();
  const create = useCreateProject();
  return (
    <Box>
      <Composer
        placeholder="Add a project — e.g. “photo gallery at ~/code/gallery”"
        onSend={async (text) => {
          const parsed = parseProjectInput(text);
          if (!parsed) {
            alert("Include a path like /Users/you/code/foo or ~/code/foo");
            return;
          }
          const p = await create.mutateAsync({
            name: parsed.name,
            path: parsed.path,
            permission_mode: "acceptEdits",
            dangerously_skip: false,
            is_default: false,
          });
          navigate(`/projects/${p.id}`);
        }}
      />
      <Text fontSize="2xs" color="fg.subtle" px={4} pb={1}>
        Include a path (e.g. <Box as="code">~/code/foo</Box>). Tilde is expanded server-side.
      </Text>
    </Box>
  );
}
