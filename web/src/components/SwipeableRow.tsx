import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Box, Flex, IconButton, Text } from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

/**
 * Row wrapper with delete affordance.
 *
 *   Mobile (touch):  swipe the row left → red "Delete" pane reveals → tap.
 *   Desktop (mouse): always-visible muted trash button to the right of the
 *                    row. Click → confirm → delete.
 *
 * The two paths are structurally separate (mobile is the swipe stack, the
 * desktop button is a sibling flex item). Nothing overlays the row's own
 * content, so the inner row's click handlers (e.g. "navigate to detail")
 * stay clean.
 */
interface Props {
  children: ReactNode;
  /** Caller does the actual mutation. */
  onDelete: () => void | Promise<void>;
  /** Optional confirmation message via window.confirm. */
  confirmMessage?: string;
  /** Hide the delete affordance for non-deletable rows. */
  disabled?: boolean;
}

const REVEAL_PX = 96;
const OPEN_THRESHOLD = 36;
const SWIPE_LOCK_PX = 8;
const ACTIVATE_PX = REVEAL_PX * 0.75;

export function SwipeableRow({
  children,
  onDelete,
  confirmMessage,
  disabled = false,
}: Props) {
  if (disabled) {
    return <Box>{children}</Box>;
  }
  return (
    <Flex align="stretch" gap={1.5}>
      <Box flex="1" minW={0}>
        <MobileSwipeable onDelete={onDelete} confirmMessage={confirmMessage}>
          {children}
        </MobileSwipeable>
      </Box>
      <DesktopDeleteButton onDelete={onDelete} confirmMessage={confirmMessage} />
    </Flex>
  );
}

// --------------------------------- mobile -------------------------------- //

function MobileSwipeable({
  children,
  onDelete,
  confirmMessage,
}: {
  children: ReactNode;
  onDelete: Props["onDelete"];
  confirmMessage?: string;
}) {
  const [open, setOpen] = useState(false);
  const [dx, setDx] = useState(0);
  const dragging = useRef(false);
  const startX = useRef<number | null>(null);
  const startY = useRef<number | null>(null);
  const openAtPointerDown = useRef(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const reset = useCallback(() => {
    setOpen(false);
    setDx(0);
    dragging.current = false;
    startX.current = null;
    startY.current = null;
  }, []);

  // Close when tapping outside.
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | TouchEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        reset();
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("touchstart", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("touchstart", close);
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

  const onPointerDown = (e: React.PointerEvent) => {
    // Only touch on mobile path.
    if (e.pointerType !== "touch") return;
    startX.current = e.clientX;
    startY.current = e.clientY;
    dragging.current = false;
    openAtPointerDown.current = open;
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (e.pointerType !== "touch") return;
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
    setDx(Math.max(-REVEAL_PX * 1.2, Math.min(0, base + deltaX)));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (e.pointerType !== "touch") return;
    if (!dragging.current) {
      if (openAtPointerDown.current) reset();
      startX.current = null;
      return;
    }
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    if (-dx >= ACTIVATE_PX) void handleDelete();
    else if (-dx >= OPEN_THRESHOLD) {
      setOpen(true);
      setDx(-REVEAL_PX);
    } else reset();
    startX.current = null;
    startY.current = null;
    dragging.current = false;
  };

  const onClickCapture = (e: React.MouseEvent) => {
    // After swiping open and tapping the row to close: eat the click so the
    // inner onClick (e.g. navigate) doesn't fire.
    if (openAtPointerDown.current) {
      e.stopPropagation();
      e.preventDefault();
      openAtPointerDown.current = false;
    }
  };

  return (
    <Box ref={wrapRef} position="relative">
      {/* Delete pane behind row, exposed when row translates left. */}
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
      </Box>
    </Box>
  );
}

// --------------------------------- desktop ------------------------------- //

function DesktopDeleteButton({
  onDelete,
  confirmMessage,
}: {
  onDelete: Props["onDelete"];
  confirmMessage?: string;
}) {
  const handleDelete = async () => {
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    await onDelete();
  };
  return (
    <IconButton
      hideBelow="md"
      aria-label="Delete"
      size="sm"
      variant="ghost"
      color="fg.subtle"
      alignSelf="center"
      _hover={{ color: "red.fg", bg: "red.subtle" }}
      onClick={(e) => {
        e.stopPropagation();
        void handleDelete();
      }}
    >
      <LuTrash2 />
    </IconButton>
  );
}
