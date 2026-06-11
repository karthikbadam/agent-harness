/**
 * Re-exports of OpenAPI-generated types for ergonomic imports.
 *
 * Regenerate the underlying `api.ts` via:
 *   - Online (server must be up): npm run gen:types
 *   - Offline (using a dumped spec): server-side `agent-harness gen-openapi web/openapi.json` then `npm run gen:types:offline`
 */

import type { components } from "./api";

export type ProjectOut = components["schemas"]["ProjectOut"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];
export type ProjectUpdate = components["schemas"]["ProjectUpdate"];
export type PathSuggestion = components["schemas"]["PathSuggestion"];

export type JobOut = components["schemas"]["JobOut"];
export type TurnOut = components["schemas"]["TurnOut"];
export type JobCreate = components["schemas"]["JobCreate"];
export type FollowupCreate = components["schemas"]["FollowupCreate"];

export type TaskOut = components["schemas"]["TaskOut"];
export type TaskCreate = components["schemas"]["TaskCreate"];
export type TaskUpdate = components["schemas"]["TaskUpdate"];
export type OutcomeOut = components["schemas"]["OutcomeOut"];
export type LastPlanOut = components["schemas"]["LastPlanOut"];
export type ArtifactOut = components["schemas"]["ArtifactOut"];
export type LoopCreate = components["schemas"]["LoopCreate"];
export type IterationOut = components["schemas"]["IterationOut"];

export type ScheduleOut = components["schemas"]["ScheduleOut"];
export type ScheduleCreate = components["schemas"]["ScheduleCreate"];
export type ScheduleUpdate = components["schemas"]["ScheduleUpdate"];

export type AuthInfo = components["schemas"]["AuthInfo"];

export type ToolUseEvent = components["schemas"]["ToolUseEvent"];
export type ToolResultEvent = components["schemas"]["ToolResultEvent"];
export type AssistantTextEvent = components["schemas"]["AssistantTextEvent"];
export type TurnDoneEvent = components["schemas"]["TurnDoneEvent"];
export type JobStatusEvent = components["schemas"]["JobStatusEvent"];

export type StreamEvent =
  | ToolUseEvent
  | ToolResultEvent
  | AssistantTextEvent
  | TurnDoneEvent
  | JobStatusEvent;
