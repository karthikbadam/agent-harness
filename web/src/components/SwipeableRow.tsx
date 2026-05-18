import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Box, Flex, IconButton } from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

/**
 * iOS-style swipe-left-to-delete row.
 *
 * Mobile: drag the row left with a finger. Past ~40% reveal threshold the
 *   row snaps open and the delete button is hit-targetable.
 * Desktop: a small trash icon appears on hover in the top-right of the row.
 *   Reflects the Mac convention (Finder, Mail) of hover-reveal affordances.
 *
 * Implementation: pointer events (work for touch + mouse), translateX on a
 * wrapper, fixed delete pane behind. No external deps.
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

const REVEAL_PX = 92; // width of the delete pane
const OPEN_THRESHOLD = 36; // drag at least this far to snap open
const SWIPE_LOCK_PX = 8; // small horizontal movement before we capture the touch
const ACTIVATE_PX = REVEAL_PX * 0.7; // drag past this to trigger delete on release

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
    // Only capture primary button on mouse; touch is always primary.
    if (e.pointerType === "mouse" && e.button !== 0) return;
    startX.current = e.clientX;
    startY.current = e.clientY;
    dragging.current = false;
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (startX.current === null || startY.current === null) return;
    const deltaX = e.clientX - startX.current;
    const deltaY = e.clientY - startY.current;
    // Capture the gesture only when horizontal movement dominates and exceeds
    // the lock threshold. Otherwise let the page scroll vertically.
    if (
      !dragging.current &&
      Math.abs(deltaX) > SWIPE_LOCK_PX &&
      Math.abs(deltaX) > Math.abs(deltaY)
    ) {
      dragging.current = true;
      // Capture pointer so subsequent moves come to us even if leaving the
      // element bounds.
      (e.target as Element).setPointerCapture?.(e.pointerId);
    }
    if (!dragging.current) return;
    e.preventDefault();
    // Only allow leftward drag (negative dx). Allow tiny right-drag for
    // overscroll feel only when already open.
    const base = open ? -REVEAL_PX : 0;
    const next = Math.max(-REVEAL_PX * 1.2, Math.min(0, base + deltaX));
    setDx(next);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!dragging.current) {
      // Plain tap — let the inner content handle it. Reset state if open.
      if (open) {
        reset();
      }
      startX.current = null;
      return;
    }
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    const opened = -dx >= OPEN_THRESHOLD;
    if (-dx >= ACTIVATE_PX) {
      // Released past activate threshold → fire delete directly.
      void handleDelete();
    } else if (opened) {
      setOpen(true);
      setDx(-REVEAL_PX);
    } else {
      reset();
    }
    startX.current = null;
    startY.current = null;
    dragging.current = false;
  };

  return (
    <Box
      ref={wrapRef}
      position="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      // Prevent page text from being highlighted while dragging.
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
        width={`${REVEAL_PX}px`}
        bg="red.solid"
        rounded="lg"
        cursor="pointer"
        onClick={(e) => {
          e.stopPropagation();
          void handleDelete();
        }}
      >
        <Box color="white" fontSize="lg" lineHeight="0">
          <LuTrash2 />
        </Box>
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
        // Desktop hover affordance: trash icon top-right while hovered, only
        // when the row isn't already swiped open. Hidden below md so it
        // doesn't conflict with the swipe gesture on touch.
        css={{
          // Block native pull-to-refresh / page swipe interference.
          touchAction: "pan-y",
        }}
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
