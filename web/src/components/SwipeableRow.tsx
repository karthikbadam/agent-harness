import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Box, Flex, IconButton, Text } from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

/**
 * iOS-style swipe-left-to-delete row.
 *
 * Mobile flow:
 *   1. Drag the row left with a finger.
 *   2. Past ~40% reveal threshold, the row snaps open and the red Delete
 *      pane on the right becomes hit-targetable.
 *   3. Tap the Delete pane → confirm → delete.
 *   4. Tap anywhere else on the row → just close the swipe (does NOT trigger
 *      the row's own onClick like "navigate to detail"). Subtle but
 *      important — without this guard, every tap-to-close also navigates.
 *
 * Desktop flow:
 *   - A small trash icon appears on hover in the top-right of the row
 *     (Finder / Mail convention). Hidden during swipe-open state.
 */
interface Props {
  children: ReactNode;
  /** Called after the user confirms delete. Caller does the actual mutation. */
  onDelete: () => void | Promise<void>;
  /** Optional confirmation message shown via window.confirm before deleting. */
  confirmMessage?: string;
  /** Disable swipe + hover trash (e.g. for non-deletable rows). */
  disabled?: boolean;
}

const REVEAL_PX = 96; // width of the delete pane
const OPEN_THRESHOLD = 36; // drag at least this far to snap open
const SWIPE_LOCK_PX = 8; // small horizontal movement before we capture the touch
const ACTIVATE_PX = REVEAL_PX * 0.75; // drag past this on release → fire delete

export function SwipeableRow({
  children,
  onDelete,
  confirmMessage,
  disabled = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [dx, setDx] = useState(0); // current translateX while dragging
  const [hovered, setHovered] = useState(false);
  const startX = useRef<number | null>(null);
  const startY = useRef<number | null>(null);
  const dragging = useRef(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  // Snapshot of open at pointerdown — used by the click-capture guard so a
  // tap that started while open still closes (without firing children's
  // onClick) even after pointerup has reset open.
  const openAtPointerDown = useRef(false);

  const reset = useCallback(() => {
    setOpen(false);
    setDx(0);
    startX.current = null;
    startY.current = null;
    dragging.current = false;
  }, []);

  // Click anywhere outside the row to close the revealed state.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent | TouchEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        reset();
      }
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [open, reset]);

  const handleDelete = useCallback(async () => {
    if (confirmMessage && !window.confirm(confirmMessage)) {
      reset();
      return;
    }
    await onDelete();
    reset();
  }, [confirmMessage, onDelete, reset]);

  if (disabled) {
    return <Box>{children}</Box>;
  }

  // Pointer handlers — work for touch and mouse alike.
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    startX.current = e.clientX;
    startY.current = e.clientY;
    dragging.current = false;
    openAtPointerDown.current = open;
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (startX.current === null || startY.current === null) return;
    const deltaX = e.clientX - startX.current;
    const deltaY = e.clientY - startY.current;
    if (
      !dragging.current &&
      Math.abs(deltaX) > SWIPE_LOCK_PX &&
      Math.abs(deltaX) > Math.abs(deltaY)
    ) {
      dragging.current = true;
      (e.target as Element).setPointerCapture?.(e.pointerId);
    }
    if (!dragging.current) return;
    e.preventDefault();
    const base = open ? -REVEAL_PX : 0;
    const next = Math.max(-REVEAL_PX * 1.2, Math.min(0, base + deltaX));
    setDx(next);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!dragging.current) {
      // Plain tap (no drag).
      if (openAtPointerDown.current) {
        // Tap-to-close while open. The click-capture handler below catches
        // the subsequent click and prevents the inner onClick from firing.
        reset();
      }
      startX.current = null;
      return;
    }
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    if (-dx >= ACTIVATE_PX) {
      void handleDelete();
    } else if (-dx >= OPEN_THRESHOLD) {
      setOpen(true);
      setDx(-REVEAL_PX);
    } else {
      reset();
    }
    startX.current = null;
    startY.current = null;
    dragging.current = false;
  };

  // Capture-phase click guard: if a click lands on the row while it WAS open
  // at pointerdown, stop it before it reaches children. The user is closing
  // the swipe, not invoking the row's own onClick.
  const onClickCapture = (e: React.MouseEvent) => {
    if (openAtPointerDown.current) {
      e.stopPropagation();
      e.preventDefault();
      openAtPointerDown.current = false;
    }
  };

  return (
    <Box
      ref={wrapRef}
      position="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{ userSelect: dragging.current ? "none" : "auto" }}
    >
      {/* Delete pane sits behind the row, revealed as the row translates left. */}
      <Flex
        position="absolute"
        top={0}
        bottom={0}
        right={0}
        align="center"
        justify="center"
        gap={1.5}
        width={`${REVEAL_PX}px`}
        bg="red.solid"
        rounded="lg"
        cursor="pointer"
        // pointerEvents must be enabled even when closed (in case finger
        // ends exactly at the edge); but we tap-test by visibility through
        // the row. When closed, the row Box covers this fully.
        onClick={(e) => {
          e.stopPropagation();
          void handleDelete();
        }}
      >
        <Box color="white" fontSize="md" lineHeight="0">
          <LuTrash2 />
        </Box>
        <Text color="white" fontSize="xs" fontWeight="medium">
          Delete
        </Text>
      </Flex>
      {/* The row itself — translates left as the user drags. */}
      <Box
        position="relative"
        transform={`translateX(${dx}px)`}
        transition={dragging.current ? "none" : "transform 0.2s ease"}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClickCapture={onClickCapture}
        css={{ touchAction: "pan-y" }}
      >
        {children}
        <IconButton
          aria-label="Delete"
          position="absolute"
          top="50%"
          right={2}
          transform="translateY(-50%)"
          size="xs"
          variant="ghost"
          color="fg.subtle"
          _hover={{ color: "red.fg", bg: "red.subtle" }}
          opacity={hovered && !open ? 1 : 0}
          pointerEvents={hovered && !open ? "auto" : "none"}
          transition="opacity 0.15s"
          hideBelow="md"
          onClick={(e) => {
            e.stopPropagation();
            void handleDelete();
          }}
        >
          <LuTrash2 />
        </IconButton>
      </Box>
    </Box>
  );
}
