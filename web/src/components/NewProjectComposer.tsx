import { Box, Text } from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";

import { Composer } from "./Composer";
import { tasksApi } from "../api/tasks";
import { useCreateProject } from "../hooks/useProjects";

/**
 * Parse a free-text project description into `{ ask, path, name }`.
 *
 * Heuristic:
 *  1. Find the first path-like token (starts with `/`, `~/`, or `./`).
 *  2. The text around the path (stripped of filler like "at", "in", "the")
 *     becomes the ask sent to the planner. Empty ask → just create the
 *     project, no planning.
 *  3. `name` is derived from the path's basename, since the ask is shown
 *     verbatim on the project's plan view.
 *
 * Examples:
 *   "/Users/me/code/blog"
 *     → { ask: "", path: ".../blog", name: "blog" }
 *   "Build a photo gallery at ~/code/gallery"
 *     → { ask: "Build a photo gallery", path: "~/code/gallery", name: "gallery" }
 */
export function parseProjectInput(
  text: string,
): { ask: string; path: string; name: string } | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const pathRe = /(^|\s)(~\/[^\s]+|\/[^\s]+|\.\/[^\s]+)/;
  const match = pathRe.exec(trimmed);
  if (!match || !match[2]) return null;
  const path: string = match[2];
  const before = trimmed.slice(0, match.index).trim();
  const after = trimmed.slice(match.index + match[0].length).trim();
  const askRaw = (before + " " + after).trim();
  // Strip the "at <path>"/"in <path>" connector tokens that wrapped the path.
  const ask = askRaw
    .replace(/\b(at|in|inside|under)\s*$/i, "")
    .replace(/^(at|in|inside|under)\s+/i, "")
    .replace(/[,;:]\s*$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const basename = path.replace(/\/+$/, "").split("/").pop() ?? path;
  return { ask, path, name: basename };
}

export function NewProjectComposer() {
  const navigate = useNavigate();
  const create = useCreateProject();
  return (
    <Box>
      <Composer
        placeholder="New project — e.g. “Build a photo gallery at ~/code/gallery”"
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
          // Navigate immediately so the user sees the project; the planner
          // call below runs in the background and the detail page polls.
          navigate(`/projects/${p.id}`);
          if (parsed.ask) {
            // Fire-and-forget the planner. We don't await because navigation
            // already happened and the detail page picks it up via polling.
            tasksApi.plan(p.id, parsed.ask).catch((err) => {
              console.error("planner failed for new project:", err);
            });
          }
        }}
      />
      <Text fontSize="2xs" color="fg.subtle" px={4} pb={1}>
        Include a path (e.g. <Box as="code">~/code/foo</Box>). Tilde is expanded server-side.
        Any prose around the path becomes the first plan.
      </Text>
    </Box>
  );
}
