import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Box, Flex, IconButton, Text } from "@chakra-ui/react";
import { LuTrash2 } from "react-icons/lu";

/**
 * Row/card wrapper with delete affordance.
 *
 *   Mobile (touch):  swipe left → red "Delete" pane reveals → tap to confirm.
 *   Desktop (mouse): a trash button appears in the top-right corner on hover.
 *                    Click once → button turns red asking for confirmation,
 *                    click again → delete fires. Moving the mouse out cancels.
 *
 * The desktop button is positioned absolutely over the card so the outer
 * layout (e.g. masonry column) is never broken by a sibling flex column.
 */
interface Props {
  children: ReactNode;
  onDelete: () => void | Promise<void>;
  confirmMessage?: string;
  /** Hide the delete affordance entirely (e.g. the __default project). */
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
  return (
    <Box position="relative" role="group">
      {disabled ? (
        children
      ) : (
        <MobileSwipeable onDelete={onDelete} confirmMessage={confirmMessage}>
          {children}
        </MobileSwipeable>
      )}
      {!disabled && (
        <HoverDeleteButton onDelete={onDelete} confirmMessage={confirmMessage} />
      )}
    </Box>
  );
}

// ─── desktop hover button ────────────────────────────────────────────────────

function HoverDeleteButton({
  onDelete,
  confirmMessage,
}: {
  onDelete: Props["onDelete"];
  confirmMessage?: string;
}) {
  const [confirming, setConfirming] = useState(false);

  const handleClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    if (!confirmMessage || window.confirm(confirmMessage)) {
      await onDelete();
    }
  };

  return (
    <IconButton
      hideBelow="md"
      aria-label="Delete"
      size="xs"
      variant="ghost"
      position="absolute"
      top={2}
      right={2}
      opacity={confirming ? 1 : 0}
      _groupHover={{ opacity: 1 }}
      transition="opacity 0.15s, background 0.15s, color 0.15s"
      color={confirming ? "red.fg" : "fg.subtle"}
      bg={confirming ? "red.subtle" : undefined}
      _hover={{ color: "red.fg", bg: "red.subtle" }}
      onMouseLeave={() => setConfirming(false)}
      onClick={handleClick}
      title={confirming ? "Click again to confirm delete" : "Delete"}
    >
      <LuTrash2 />
    </IconButton>
  );
}

// ─── mobile swipe-to-delete ──────────────────────────────────────────────────

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
    if (openAtPointerDown.current) {
      e.stopPropagation();
      e.preventDefault();
      openAtPointerDown.current = false;
    }
  };

  return (
    <Box ref={wrapRef} position="relative">
      <Flex
        display={open || dx !== 0 ? "flex" : "none"}
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
