import { KeyboardEvent, useEffect, useRef, useState } from "react";
import {
  Box,
  Flex,
  IconButton,
  Image,
  Spinner,
  Text,
} from "@chakra-ui/react";
import { LuArrowUp, LuFile, LuPaperclip, LuX } from "react-icons/lu";
import { attachmentsApi, type AttachmentOut } from "../api/attachments";

interface StagedFile {
  localId: string; // temp id for react key
  file: File;
  objectUrl: string;
  isImage: boolean;
  uploading: boolean;
  attachment?: AttachmentOut;
}

interface Props {
  placeholder?: string;
  disabled?: boolean;
  projectId?: string;
  onSend: (prompt: string, attachmentIds: string[]) => Promise<void> | void;
}

const MIN_HEIGHT = 44;
const MAX_HEIGHT = 240;
const HINT_AT = 200;

export function Composer({
  placeholder = "Tell claude...",
  disabled,
  projectId,
  onSend,
}: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.max(MIN_HEIGHT, Math.min(el.scrollHeight, MAX_HEIGHT));
    el.style.height = `${next}px`;
  }, [value]);

  const uploadsInFlight = staged.some((s) => s.uploading);

  const submit = async () => {
    const text = value.trim();
    const hasContent = text || staged.some((s) => s.attachment);
    if (!hasContent || busy || disabled || uploadsInFlight) return;
    setBusy(true);
    const ids = staged
      .filter((s) => s.attachment)
      .map((s) => s.attachment!.id);
    try {
      await onSend(text, ids);
      setValue("");
      // Revoke object URLs
      staged.forEach((s) => URL.revokeObjectURL(s.objectUrl));
      setStaged([]);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void submit();
    }
  };

  const pickFiles = () => fileRef.current?.click();

  const onFilesChosen = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const newStaged: StagedFile[] = Array.from(files).map((f) => ({
      localId: Math.random().toString(36).slice(2),
      file: f,
      objectUrl: URL.createObjectURL(f),
      isImage: f.type.startsWith("image/"),
      uploading: true,
    }));
    setStaged((prev) => [...prev, ...newStaged]);

    // Upload each file and update state as they finish
    for (const sf of newStaged) {
      try {
        const att = await attachmentsApi.upload(sf.file, projectId);
        setStaged((prev) =>
          prev.map((s) =>
            s.localId === sf.localId
              ? { ...s, uploading: false, attachment: att }
              : s,
          ),
        );
      } catch {
        setStaged((prev) =>
          prev.filter((s) => s.localId !== sf.localId),
        );
        URL.revokeObjectURL(sf.objectUrl);
      }
    }
  };

  const removeStaged = async (sf: StagedFile) => {
    URL.revokeObjectURL(sf.objectUrl);
    setStaged((prev) => prev.filter((s) => s.localId !== sf.localId));
    if (sf.attachment) {
      attachmentsApi.remove(sf.attachment.id).catch(() => {});
    }
  };

  const canSend =
    (!!value.trim() || staged.some((s) => s.attachment)) &&
    !disabled &&
    !busy &&
    !uploadsInFlight;
  const showHint = focused && value.length === 0 && staged.length === 0;
  const showCount = value.length >= HINT_AT;

  return (
    <Box
      px={3}
      pt={3}
      pb="max(env(safe-area-inset-bottom), 10px)"
      bg="bg.subtle"
    >
      {/* Attachment previews */}
      {staged.length > 0 && (
        <Flex gap={2} px={1} pb={2} flexWrap="wrap">
          {staged.map((sf) => (
            <Box key={sf.localId} position="relative" flexShrink={0}>
              {sf.isImage ? (
                <Image
                  src={sf.objectUrl}
                  w="14"
                  h="14"
                  objectFit="cover"
                  rounded="md"
                  borderWidth="1px"
                  borderColor="border.subtle"
                  opacity={sf.uploading ? 0.5 : 1}
                />
              ) : (
                <Flex
                  align="center"
                  gap={1.5}
                  px={2.5}
                  h="14"
                  bg="bg.muted"
                  rounded="md"
                  borderWidth="1px"
                  borderColor="border.subtle"
                  maxW="36"
                  opacity={sf.uploading ? 0.5 : 1}
                >
                  <Box color="fg.muted" flexShrink={0}>
                    <LuFile size={16} />
                  </Box>
                  <Text fontSize="xs" color="fg.muted" truncate>
                    {sf.file.name}
                  </Text>
                </Flex>
              )}
              {sf.uploading && (
                <Flex
                  position="absolute"
                  inset={0}
                  align="center"
                  justify="center"
                >
                  <Spinner size="xs" />
                </Flex>
              )}
              <IconButton
                aria-label="Remove"
                size="2xs"
                rounded="full"
                bg="fg"
                color="bg"
                position="absolute"
                top="-1.5"
                right="-1.5"
                minW={0}
                h="4"
                w="4"
                onClick={() => removeStaged(sf)}
                _hover={{ bg: "fg.muted" }}
              >
                <LuX size={10} />
              </IconButton>
            </Box>
          ))}
        </Flex>
      )}

      <Flex
        align="flex-end"
        gap={2}
        bg="bg"
        borderRadius="lg"
        px={3.5}
        py={2.5}
        outline={focused ? "1px solid" : undefined}
        outlineColor={focused ? "blue.solid" : undefined}
        outlineOffset="0"
        transition="outline-color 0.15s ease"
      >
        {/* Hidden file input */}
        <input
          ref={fileRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(e) => onFilesChosen(e.target.files)}
          onClick={(e) => {
            // Allow re-selecting the same file
            (e.target as HTMLInputElement).value = "";
          }}
        />

        {/* Attachment icon */}
        <IconButton
          aria-label="Attach file"
          variant="ghost"
          size="sm"
          color="fg.muted"
          alignSelf="flex-end"
          mb={0.5}
          onClick={pickFiles}
          disabled={busy}
        >
          <LuPaperclip />
        </IconButton>

        <Box
          as="textarea"
          ref={textareaRef}
          rows={1}
          placeholder={placeholder}
          value={value}
          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
            setValue(e.target.value)
          }
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          disabled={disabled || busy}
          resize="none"
          border="none"
          outline="none"
          bg="transparent"
          px={0}
          py={1}
          flex="1"
          minH={`${MIN_HEIGHT}px`}
          maxH={`${MAX_HEIGHT}px`}
          fontSize="16px"
          lineHeight="1.4"
          fontFamily="inherit"
          color="inherit"
          sx={{
            "&:focus": { boxShadow: "none", outline: "none" },
            "&::placeholder": { color: "var(--chakra-colors-fg-subtle)" },
          }}
        />
        <IconButton
          aria-label="Send"
          onClick={submit}
          loading={busy}
          disabled={!canSend}
          size="sm"
          rounded="full"
          colorPalette="blue"
          variant={canSend ? "solid" : "subtle"}
          alignSelf="flex-end"
          mb={1}
        >
          <LuArrowUp />
        </IconButton>
      </Flex>
      {(showHint || showCount) && (
        <Flex
          justify="space-between"
          px={2}
          pt={1}
          fontSize="xs"
          color="fg.muted"
        >
          <Text>{showHint ? "↵ for newline · ⌘↵ to send" : ""}</Text>
          {showCount && <Text>{value.length} chars</Text>}
        </Flex>
      )}
    </Box>
  );
}
